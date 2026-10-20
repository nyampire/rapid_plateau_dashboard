def test_threshold_is_same_in_api_and_ingest():
    import compute_stats, dashboard_api
    assert dashboard_api.RATE_DONE_THRESHOLD == compute_stats.RATE_DONE_THRESHOLD == 90
