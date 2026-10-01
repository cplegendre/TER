"""Command implementation module extracted from :mod:`ter_calculator.cli`."""

from __future__ import annotations

import sys
from pathlib import Path


def _cmd_report(args) -> int:
    """Markdown one-screen summary for humans."""
    # Resolve --latest flag
    if args.latest:
        from ..loader import find_latest_session

        args.session_path = str(find_latest_session(args.session_path))
        if not args.quiet:
            print(f"Using latest session: {args.session_path}", file=sys.stderr)
    elif args.session_path is None:
        print("Error: Either provide a session_path or use --latest", file=sys.stderr)
        return 1

    from ..analyze_pipeline import analyze_session
    from ..session_report import format_session_report_markdown

    result = analyze_session(args)
    html_out = getattr(args, "report_html", None)
    if html_out:
        from ter.adapters.driving.reports import render_report_html
        from ter.adapters.driving.reports.ter3 import from_ter_result

        html_path = Path(html_out)
        if html_path.parent != Path():
            html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            render_report_html(from_ter_result(result)), encoding="utf-8"
        )
        if not args.quiet:
            print(f"Wrote {html_path}", file=sys.stderr)

    out = getattr(args, "report_output", None)
    if html_out and not out:
        return 0
    md = format_session_report_markdown(result)
    if out:
        Path(out).write_text(md, encoding="utf-8")
        if not args.quiet:
            print(f"Wrote {out}", file=sys.stderr)
    else:
        print(md)
    return 0
