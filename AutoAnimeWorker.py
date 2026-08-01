"""Run the persistent AutoAnime Worker."""

import argparse
import os
import socket
import time
from pathlib import Path

from autoanime_v3.jobs.queue import JobQueue
from autoanime_v3.jobs.worker import Worker
from autoanime_v3.services.automation import AutomationRuntime
from autoanime_v3.services.operations import OperationService
from autoanime_v3.services.scans import ScanService


def main(argv=None):
    parser = argparse.ArgumentParser(description="AutoAnime Worker")
    parser.add_argument("--data-dir", type=Path, default=Path("C:/ProgramData/AutoAnime"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    data_directory = args.data_dir.resolve()
    database = data_directory / "data" / "library.sqlite3"
    queue = JobQueue(database)

    def scan_handler(job):
        queue.append_event(job.id, "phase", {"name": "scan"}, "开始扫描")
        outcome = ScanService(database).run(int(job.payload["profile_id"]), job.payload.get("paths") or None)
        queue.append_event(job.id, "scan_completed", {"plan_id": outcome.plan_id}, "扫描完成")

    def execute_handler(job):
        queue.append_event(job.id, "phase", {"name": "execute"}, "开始执行计划")
        batch = OperationService(database, data_directory / "operations").execute(int(job.payload["plan_id"]))
        queue.append_event(job.id, "execution_completed", {"batch_id": batch.id}, "执行完成")

    def rollback_handler(job):
        queue.append_event(job.id, "phase", {"name": "rollback"}, "开始安全回滚")
        batch = OperationService(database, data_directory / "operations").rollback(
            int(job.payload["batch_id"]), job.payload.get("requested_by")
        )
        queue.append_event(job.id, "rollback_completed", {"batch_id": batch.id}, "回滚完成")

    worker = Worker(
        "%s:%s" % (socket.gethostname(), os.getpid()),
        queue,
        {
            "scan": scan_handler,
            "execute_plan": execute_handler,
            "rollback_operation": rollback_handler,
        },
    )
    automation = AutomationRuntime(
        database,
        queue=queue,
        watch_poll_seconds=max(0.05, args.poll_seconds),
        observer_reload_seconds=max(0.25, args.poll_seconds),
    )
    automation.start()
    try:
        if args.once:
            automation.tick()
            worker.run_once(lease_seconds=120)
            return
        while True:
            automation.tick()
            if worker.run_once(lease_seconds=120) is None:
                time.sleep(max(0.1, args.poll_seconds))
    except KeyboardInterrupt:
        return
    finally:
        automation.stop()


if __name__ == "__main__":
    main()
