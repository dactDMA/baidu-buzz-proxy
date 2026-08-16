from baidu_buzz_proxy.web import job_html


def test_job_page_has_live_progress_feedback() -> None:
    page = job_html("job-id")

    assert 'class="progress-track indeterminate"' in page
    assert "one or two minutes" in page
    assert "Current step:" in page
    assert "progress-slide" in page
    assert "MiB/s" in page
    assert "setTimeout(refresh,1000)" in page
