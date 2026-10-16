"""usage — who uses Pinakes, which tools they use, and where they are.

    capture.py      records each request into usage.db (off unless
                    PINAKES_USAGE_TRACKING is set)
    tools.py        maps endpoints to areas and named tools
    ua.py           parses User-Agent into browser/OS/device and bot kind
    geo.py          IP → country/region/city/ASN from local .mmdb files
    netinfo.py      reverse DNS, RDAP and ROR lookups, network classes
    institutions.py the domain and name indexes that identify institutions
    resolve.py      nightly: resolve IPs, classify visitors, delete raw IPs
    refresh.py      downloads the IP databases and institution lists
    report.py       the numbers behind /admin/usage and the grant report

Run `python -m usage --help` for the command-line jobs.
"""

from __future__ import annotations


def init_app(app) -> None:
    """Register the capture hooks. before_request is inserted first so the
    timing covers the whole request, including the other hooks."""
    from . import capture
    app.before_request_funcs.setdefault(None, []).insert(0, capture.before_request)
    app.after_request(capture.after_request)

    @app.context_processor
    def _usage_globals():
        from .resolve import raw_ip_hours
        from .survey import enabled as survey_enabled
        return {"usage_tracking": capture.enabled(), "usage_raw_ip_hours": raw_ip_hours(),
                "usage_survey": survey_enabled()}
