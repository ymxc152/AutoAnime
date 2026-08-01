"""Schedule producer; it can only enqueue jobs."""


class Scheduler:
    def __init__(self, queue):
        self.queue = queue

    def enqueue_due(self, profile_id, schedule_id, occurrence):
        return self.queue.enqueue(
            "scan",
            {"profile_id": profile_id, "trigger": "schedule", "schedule_id": schedule_id},
            "schedule:%s:%s" % (schedule_id, occurrence),
        )

