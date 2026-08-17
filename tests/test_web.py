from baidu_buzz_proxy.web import index_html, job_html


def test_homepage_links_to_the_source_repository() -> None:
    page = index_html("")

    assert 'href="https://github.com/dactDMA/baidu-buzz-proxy"' in page
    assert ">Open source under MIT</a>" in page


def test_job_page_has_live_progress_feedback() -> None:
    page = job_html("job-id")

    assert 'class="progress-track indeterminate"' in page
    assert "one or two minutes" in page
    assert "Current step:" in page
    assert "progress-slide" in page
    assert "MiB/s" in page
    assert "Streaming file " in page
    assert "Exact sizes are read in batches" in page
    assert "setTimeout(refresh,1000)" in page
    assert 'id="donation" class="panel donation" hidden' in page
    assert "About $20 per month keeps the server online" in page
    assert "0xAAD4489D08215846D273c9644575c353a1dF0138" in page
    assert "TEqBcdPt66wHNnWaz6i4ZAYby8EmLgBoMv" in page
    assert "bc1qm4mw98du94ldnxezm0cs7h4rk2g6ev3wsnjjeh" in page
    assert "navigator.clipboard.writeText" in page
