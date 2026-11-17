import argparse
import sys

# Force UTF-8 output on Windows to handle Unicode job titles
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from storage.database import init_db


def main():
    parser = argparse.ArgumentParser(
        description="JobPilot AI — Automated Job Search Agent",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # --run
    sub.add_parser("run",      help="Run full pipeline once")
    sub.add_parser("schedule", help="Run pipeline on interval (config.yaml)")

    # export
    exp = sub.add_parser("export", help="Export DB to Excel")
    exp.add_argument("--all",       action="store_true", help="Include all jobs (not just matched)")
    exp.add_argument("--min-score", type=int, default=7, help="Minimum score (default 7)")

    # status update
    upd = sub.add_parser("status", help="Update application status for a job")
    upd.add_argument("job_id", help="6-char ID from alerts/Excel, or full job_id")
    upd.add_argument("status", choices=["new","emailed","applied","skipped","response","interview","offer","rejected"])

    # list jobs
    lst = sub.add_parser("list", help="Print top matched jobs in terminal")
    lst.add_argument("--min-score", type=int, default=7)
    lst.add_argument("--limit",     type=int, default=20)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    init_db()

    if args.command == "run":
        from scheduler import run_pipeline
        run_pipeline()

    elif args.command == "schedule":
        from scheduler import start_scheduler
        start_scheduler()

    elif args.command == "export":
        from storage.excel_export import export_to_excel
        export_to_excel(min_score=args.min_score, include_all=args.all)

    elif args.command == "status":
        from storage.database import update_status, find_by_short_id
        job = find_by_short_id(args.job_id) if len(args.job_id) <= 8 else None
        update_status(job["job_id"] if job else args.job_id, args.status)
        print(f"Updated job {args.job_id} → {args.status}")

    elif args.command == "list":
        from storage.database import list_jobs
        jobs = list_jobs(min_score=args.min_score, limit=args.limit)
        if not jobs:
            print("No matching jobs found.")
            return
        print(f"\n{'ID':<8} {'Score':<6} {'Posted':<11} {'Status':<10} {'Source':<11} {'Title':<40} Company")
        print("-" * 115)
        for j in jobs:
            print(
                f"{j.get('short_id') or j['job_id'][:6]:<8} {j['relevance_score']:<6} {str(j.get('date_posted') or '?')[:10]:<11} "
                f"{j.get('status','new'):<10} {str(j.get('source',''))[:10]:<11} {j['title'][:38]:<40} {j['company']}"
            )
        print()


if __name__ == "__main__":
    main()
