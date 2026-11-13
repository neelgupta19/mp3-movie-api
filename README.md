MP3: Movie Data API with SQLite and Caching

This project is a FastAPI application that stores a movie collection in a SQLite database and serves it through a REST API. When a CSV is uploaded via POST /upload, the app verifies that it contains the exact columns title, release_year, rating, and genre before replacing the database records in a single transaction. Because the data is saved directly to a local file, the movie list stays intact even if the server restarts. Users can fetch movies with GET /movies to get a limited list and total count, or use GET /movies/search to filter by a minimum rating, sort the results, and pick specific columns. Calling POST /movies/process runs the stored movies through a data cleaning pipeline to remove duplicates, fix missing values, and filter out outliers before saving the updated data back to the database. To keep things running fast, the app caches GET /movies responses in FakeRedis for 90 seconds and automatically clears the cache whenever new data is uploaded or cleaned. Finally, custom middleware logs every request with its ID, status code, and how long it took to run.

To start the API, run this from the project folder, then open 127.0.0.1:8000/docs in a browser:

python -m uvicorn main:app --reload

To run the tests:

python -m pytest tests -v

Sample processing request: after uploading fixtures/sample_movies.csv, send this body to POST /movies/process:

{"validation": {"required_columns": ["title", "release_year", "rating", "genre"], "numeric_columns": ["release_year", "rating"]}, "processing": {"remove_duplicates": true, "missing": {"enabled": true, "axis": "rows"}, "outliers": {"enabled": true, "columns": ["rating"], "method": "iqr", "threshold": 1.5}}}

Expected result:

{"validation_rows_removed": 0, "cleaning_report": {"rows_before": 20, "rows_after": 16, "rows_removed": 4, "columns_before": 4, "columns_after": 4, "columns_removed": 0}}