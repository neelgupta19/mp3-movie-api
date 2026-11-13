import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from data_pipeline import create_cleaning_report, process_data, validate_dataframe
from src import (
    HealthResponse,
    MovieCache,
    MovieDataResponse,
    MovieProcessingReport,
    MovieStorage,
    MovieUploadInfo,
    ProcessConfig,
    log_requests,
    setup_logging,
)
from src.storage import MOVIE_COLUMNS

setup_logging(verbose=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Movie Data API", version="1.0.0")
app.middleware("http")(log_requests)
storage = MovieStorage()
cache = MovieCache()


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Return the application status and version."""
    return HealthResponse(status="ok", version=app.version)


@app.post("/upload", response_model=MovieUploadInfo)
async def upload_movies(file: UploadFile = File(...)):
    """Read a movie CSV and replace the stored collection with it."""
    # 1. Same .csv check and CSV parsing as MP2
    filename = file.filename or ""
    extension = Path(filename).suffix.lower()
    if extension != ".csv":
        shown = extension or "(no extension)"
        logger.warning("Unsupported file format: %s", shown)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format {shown}. Please upload a .csv file.",
        )

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as error:
        logger.warning("Could not read CSV upload %s: %s", filename, error)
        raise HTTPException(
            status_code=400,
            detail=f"Could not read {filename} as a CSV file: {error}",
        ) from error

    # 2. Quick validation: exactly the four movie columns and at least one row
    if set(df.columns) != set(MOVIE_COLUMNS) or len(df.columns) != len(MOVIE_COLUMNS):
        logger.warning("Wrong columns in %s: %s", filename, list(df.columns))
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have exactly these columns: {', '.join(MOVIE_COLUMNS)}",
        )
    if df.empty:
        logger.warning("No data rows in %s", filename)
        raise HTTPException(status_code=400, detail="CSV has no data rows.")

    df = df[MOVIE_COLUMNS]

    # 3. Replace the stored collection, then clear outdated cached responses
    storage.save(df)
    cache.invalidate()
    logger.info("Uploaded movies: filename=%s, rows=%s", filename, len(df))
    return MovieUploadInfo(
        filename=filename,
        rows=len(df),
        columns=len(df.columns),
        column_names=list(df.columns),
    )


@app.get("/movies", response_model=MovieDataResponse)
def get_movies(limit: int = Query(default=100, ge=1)):
    """Return up to limit movies plus the total number stored."""
    # 1. Return the cached response if there is one
    cached = cache.get(limit)
    if cached is not None:
        logger.info("Cache hit: limit=%s", limit)
        return cached

    # Part 1's SQL retrieval and count
    data = storage.select_columns(MOVIE_COLUMNS, limit)
    total_rows = storage.count_movies()
    response = {"data": data, "total_rows": total_rows}

    # 2. Cache the complete response before returning it
    cache.set(limit, response)
    logger.info("Retrieved movies: returned_rows=%s", len(data))
    return response


@app.get("/movies/search", response_model=list[dict])
def query_movies(
    columns: list[str] = Query(default=["title"]),
    min_rating: float = Query(default=0, ge=0, le=10),
    limit: int = Query(default=100, ge=1),
):
    """Return id plus the chosen columns for movies rated at least min_rating."""
    try:
        results = storage.query_movies(columns, min_rating, limit)
    except ValueError as error:
        logger.warning("Bad search request: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error

    logger.info(
        "Queried movies: columns=%s, min_rating=%s, returned_rows=%s",
        columns,
        min_rating,
        len(results),
    )
    return results


@app.post("/movies/process", response_model=MovieProcessingReport)
def process_movies(config: ProcessConfig):
    """Validate and clean the stored movies with the MP1 pipeline."""
    # 1. Retrieve the stored movies
    df = storage.get()
    if df.empty:
        logger.warning("No movies to process")
        raise HTTPException(status_code=400, detail="No movies stored. Upload a CSV first.")

    # 2. Same configuration, validation, and processing as MP2
    config_dict = config.model_dump()
    required_columns = config_dict["validation"]["required_columns"]
    numeric_columns = config_dict["validation"]["numeric_columns"]

    missing_numeric = [column for column in numeric_columns if column not in df.columns]
    if missing_numeric:
        logger.warning("Numeric column(s) not found: %s", missing_numeric)
        raise HTTPException(
            status_code=400,
            detail=f"Numeric column(s) not found in dataset: {', '.join(missing_numeric)}",
        )

    try:
        rows_before_validation = len(df)
        validated = validate_dataframe(df, required_columns, numeric_columns)
        validation_rows_removed = rows_before_validation - len(validated)

        validated_copy = validated.copy()
        processed = process_data(validated, config_dict)
        cleaning_report = create_cleaning_report(validated_copy, processed)
    except ValueError as error:
        logger.warning("Processing failed: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error

    # 3. Only reached if everything above worked: save, then clear the cache
    storage.save(processed)
    cache.invalidate()
    logger.info(
        "Processed movies: validation_removed=%s, rows_before=%s, rows_after=%s",
        validation_rows_removed,
        len(validated_copy),
        len(processed),
    )
    return {
        "validation_rows_removed": validation_rows_removed,
        "cleaning_report": cleaning_report,
    }