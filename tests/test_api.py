from pathlib import Path

import pandas as pd
import pytest

from src import MovieStorage
import main

MOVIE_COLUMNS = ["title", "release_year", "rating", "genre"]


def test_movies_persist(client, test_storage, uploaded_movies):
    # A second MovieStorage pointing at the same file reads what the API saved
    reopened = MovieStorage(test_storage.db_path)
    df = reopened.get()

    assert reopened.count_movies() == 20
    assert len(df) == 20
    assert list(df.columns) == MOVIE_COLUMNS

    original = pd.read_csv(Path("fixtures") / "sample_movies.csv")
    assert list(df["title"]) == list(original["title"])

    # IDs from storage and from the API both run 1 through 20 in row order
    stored_ids = [row["id"] for row in reopened.select_columns(["title"], limit=100)]
    response = client.get("/movies", params={"limit": 100})
    assert response.status_code == 200
    api_ids = [record["id"] for record in response.json()["data"]]

    assert stored_ids == list(range(1, 21))
    assert api_ids == list(range(1, 21))


def test_cache_and_processing(client, uploaded_movies, processing_body):
    # 1. Two different limits are cached separately
    first_100 = client.get("/movies", params={"limit": 100})
    first_1 = client.get("/movies", params={"limit": 1})
    assert first_100.status_code == 200
    assert first_1.status_code == 200
    assert main.cache.get(100) == first_100.json()
    assert main.cache.get(1) == first_1.json()

    # 2. Repeating limit=100 gives the same response
    repeat_100 = client.get("/movies", params={"limit": 100})
    assert repeat_100.status_code == 200
    assert repeat_100.json() == first_100.json()

    # 3. Processing removes 4 rows and clears both cache entries
    response = client.post("/movies/process", json=processing_body)
    assert response.status_code == 200
    report = response.json()
    assert report["validation_rows_removed"] == 0
    assert report["cleaning_report"]["rows_before"] == 20
    assert report["cleaning_report"]["rows_after"] == 16
    assert report["cleaning_report"]["rows_removed"] == 4
    assert main.cache.get(100) is None
    assert main.cache.get(1) is None

    # 4. The next retrieval reads the 16 processed rows and caches them
    after = client.get("/movies", params={"limit": 100})
    assert after.status_code == 200
    body = after.json()
    assert body["total_rows"] == 16
    titles = [record["title"] for record in body["data"]]
    assert titles == [f"Movie {letter}" for letter in "ABCDEFGHIJKLMNOP"]
    assert [record["id"] for record in body["data"]] == list(range(1, 17))
    assert main.cache.get(100) == body


def test_cache_hit_skips_database(client, uploaded_movies, monkeypatch):
    # 1. Populate the cache
    first = client.get("/movies", params={"limit": 100})
    assert first.status_code == 200

    # 2. Make any database read fail the test
    def fail_if_called(*args, **kwargs):
        pytest.fail("The database was queried even though the response was cached")

    monkeypatch.setattr(main.storage, "select_columns", fail_if_called)
    monkeypatch.setattr(main.storage, "count_movies", fail_if_called)

    # 3. The repeat request must come from the cache
    second = client.get("/movies", params={"limit": 100})
    assert second.status_code == 200
    assert second.json() == first.json()