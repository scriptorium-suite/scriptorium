from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from scriptorium import __version__
from scriptorium.doctor import DoctorError
from scriptorium.pull import SUMMARY_FIELDS, PullError
from scriptorium.status import StatusError, format_status_report, run_status


def doctor_report(*, ready: bool = True) -> dict:
    status = "ready" if ready else "incomplete"
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "target": "public-alpha",
        "status": status,
        "exit_code": 0 if ready else 1,
        "readiness": {
            "demo": "ready",
            "public_alpha": status,
            "literature": "file-only",
            "slides": "unavailable",
            "web_history": "manual",
        },
        "summary": {"fail": 0 if ready else 2},
    }


def pull_report(
    *,
    status: str = "noop",
    exit_code: int = 0,
    counts: dict[str, int] | None = None,
    actions: list[dict] | None = None,
    errors: list[dict] | None = None,
    scan_codex: bool = True,
) -> dict:
    summary = {key: 0 for key in SUMMARY_FIELDS}
    summary.update(counts or {})
    return {
        "format_version": 1,
        "generated_by": {"name": "scriptorium", "version": __version__},
        "operation": "pull",
        "mode": "preview",
        "status": status,
        "exit_code": exit_code,
        "summary": summary,
        "action_required": actions or [],
        "errors": errors or [],
        "egress": {
            "suite_managed": "not-requested",
            "host_managed": "not-invoked",
            "optional_connectors": "not-invoked",
        },
        "entry": {"scan_codex": scan_codex},
    }


class StatusTests(unittest.TestCase):
    def run_with(
        self, doctor: dict, pull: dict | None = None
    ) -> tuple[dict, mock.Mock]:
        pull_mock = mock.Mock(return_value=pull)
        with (
            mock.patch("scriptorium.status.run_doctor", return_value=doctor),
            mock.patch("scriptorium.status.run_pull", pull_mock),
        ):
            report = run_status(
                workspace=Path("workspace"),
                provenance_home=Path("provenance-home"),
                project="configured-project",
            )
        return report, pull_mock

    def test_empty_preview_is_ready_and_run_confirmation_is_not_attention(self):
        report, pull_mock = self.run_with(
            doctor_report(),
            pull_report(status="planned", actions=[{"type": "run-confirmation"}]),
        )

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["freshness"]["state"], "current")
        self.assertEqual(report["freshness"]["basis"], "pull-preview")
        self.assertEqual(report["action_required"], [])
        self.assertEqual(report["egress"]["host_managed"], "readiness-probes-only")
        self.assertEqual(report["workflow"]["codex_scan"], "enabled")
        self.assertFalse(pull_mock.call_args.kwargs["run"])
        self.assertEqual(pull_mock.call_args.kwargs["project"], "configured-project")

    def test_missing_codex_home_is_attention_not_an_internal_error(self):
        report, _pull_mock = self.run_with(
            doctor_report(),
            pull_report(
                status="action-required",
                counts={"codex_found": 0},
                actions=[{"type": "codex-home-setup", "count": 1}],
            ),
        )

        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["freshness"]["state"], "review-required")
        self.assertIn(
            {"type": "codex-home-setup", "count": 1},
            report["action_required"],
        )

    def test_pending_change_is_attention_and_only_suggests_reviewing_pull(self):
        report, _pull_mock = self.run_with(
            doctor_report(),
            pull_report(
                status="planned",
                counts={"notes_planned": 2},
                actions=[{"type": "run-confirmation"}],
            ),
        )

        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["freshness"]["state"], "changes-pending")
        self.assertEqual(
            report["action_required"],
            [
                {
                    "type": "review-pull-plan",
                    "count": 1,
                    "command": "scriptorium pull",
                }
            ],
        )
        self.assertNotIn("--run", json.dumps(report))

    def test_review_actions_are_deterministic_and_content_free(self):
        report, _pull_mock = self.run_with(
            doctor_report(),
            pull_report(
                status="action-required",
                counts={
                    "notes_unstable": 4,
                    "planned_applies": 1,
                    "unresolved_events": 2,
                    "pending_fill": 1,
                    "pending_approval": 3,
                },
                actions=[
                    {"type": "human-approval", "count": 3},
                    {"type": "workspace-review", "count": 4},
                    {"type": "agent-fill", "count": 1},
                    {"type": "project-resolution", "count": 2},
                    {"type": "run-confirmation"},
                ],
            ),
        )

        self.assertEqual(report["status"], "attention")
        self.assertEqual(report["freshness"]["state"], "review-required")
        self.assertEqual(
            [item["type"] for item in report["action_required"]],
            [
                "project-resolution",
                "agent-fill",
                "human-approval",
                "workspace-review",
                "review-pull-plan",
            ],
        )
        commands = {
            item["type"]: item.get("command")
            for item in report["action_required"]
        }
        self.assertEqual(commands["review-pull-plan"], "scriptorium pull")
        self.assertIsNone(commands["project-resolution"])
        self.assertIsNone(commands["agent-fill"])
        self.assertIsNone(commands["human-approval"])
        self.assertIsNone(commands["workspace-review"])

    def test_incomplete_readiness_skips_pull(self):
        report, pull_mock = self.run_with(doctor_report(ready=False))

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["freshness"]["state"], "unknown")
        self.assertEqual(report["workflow"], {})
        self.assertEqual(report["action_required"][0]["type"], "doctor-remediation")
        pull_mock.assert_not_called()

    def test_blocked_and_error_pull_states_are_preserved(self):
        blocked, _ = self.run_with(
            doctor_report(),
            pull_report(
                status="blocked",
                exit_code=1,
                errors=[{"code": "worker_busy"}],
            ),
        )
        error, _ = self.run_with(
            doctor_report(),
            pull_report(
                status="error",
                exit_code=2,
                errors=[{"code": "stage_failed", "count": 2}],
            ),
        )

        self.assertEqual((blocked["status"], blocked["exit_code"]), ("blocked", 1))
        self.assertEqual((error["status"], error["exit_code"]), ("error", 2))
        self.assertEqual(blocked["freshness"]["basis"], "not-available")
        self.assertEqual(error["freshness"]["basis"], "not-available")
        self.assertEqual(
            blocked["action_required"][-1],
            {
                "type": "pull-diagnostics",
                "count": 1,
                "command": "scriptorium pull",
            },
        )
        self.assertEqual(error["action_required"][-1]["count"], 2)

    def test_unknown_content_and_paths_are_not_forwarded(self):
        sentinel = r"D:\private\secret-project"
        doctor = doctor_report()
        doctor["checks"] = [{"details": {"path": sentinel}, "summary": sentinel}]
        doctor["limitations"] = [sentinel]
        pull = pull_report()
        pull["entry"]["private_path"] = sentinel
        pull["unknown"] = {"research_text": sentinel}
        pull["limitations"] = [sentinel]

        report, _pull_mock = self.run_with(doctor, pull)

        serialized = json.dumps(report)
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("checks", report)
        self.assertNotIn("unknown", report)

    def test_invalid_count_and_disagreeing_action_fail_closed(self):
        invalid = pull_report(counts={"pending_fill": True})
        with self.assertRaises(StatusError):
            self.run_with(doctor_report(), invalid)

        mismatch = pull_report(
            counts={"pending_fill": 2},
            actions=[{"type": "agent-fill", "count": 1}],
        )
        with self.assertRaises(StatusError):
            self.run_with(doctor_report(), mismatch)

        unknown_error = pull_report(
            status="error",
            exit_code=2,
            errors=[{"code": "private-error"}],
        )
        with self.assertRaises(StatusError):
            self.run_with(doctor_report(), unknown_error)

    def test_workspace_review_includes_draft_only_count(self):
        report, _pull_mock = self.run_with(
            doctor_report(),
            pull_report(
                status="action-required",
                counts={"notes_unstable": 2, "draft_only": 3},
                actions=[{"type": "workspace-review", "count": 5}],
            ),
        )

        action = report["action_required"][0]
        self.assertEqual(action["type"], "workspace-review")
        self.assertEqual(action["count"], 5)

    def test_duplicate_run_confirmation_and_probe_errors_fail_closed(self):
        duplicate = pull_report(
            actions=[
                {"type": "run-confirmation"},
                {"type": "run-confirmation"},
            ]
        )
        with self.assertRaises(StatusError):
            self.run_with(doctor_report(), duplicate)

        with (
            mock.patch(
                "scriptorium.status.run_doctor",
                side_effect=DoctorError("private doctor path"),
            ),
            self.assertRaisesRegex(StatusError, "doctor probe failed"),
        ):
            run_status(
                workspace=Path("workspace"),
                provenance_home=Path("provenance-home"),
            )

        with (
            mock.patch("scriptorium.status.run_doctor", return_value=doctor_report()),
            mock.patch(
                "scriptorium.status.run_pull",
                side_effect=PullError("private pull path"),
            ),
            self.assertRaisesRegex(StatusError, "pull preview failed"),
        ):
            run_status(
                workspace=Path("workspace"),
                provenance_home=Path("provenance-home"),
            )

    def test_human_report_contains_only_aggregate_state(self):
        report, _pull_mock = self.run_with(
            doctor_report(),
            pull_report(counts={"pending_fill": 1}),
        )
        report["path_selection"] = {
            "workspace": {
                "source": "suite-config",
                "environment": None,
                "suite_config_conflict": False,
            }
        }
        report["warnings"] = []
        rendered = format_status_report(report)

        self.assertIn("Overall: ATTENTION", rendered)
        self.assertIn("pending_fill=1", rendered)
        self.assertIn("raw content and local paths suppressed", rendered)
        self.assertIn("external probe side effects not OS-observed", rendered)
        self.assertIn("workspace: suite-config", rendered)
        self.assertNotIn("configured-project", rendered)


if __name__ == "__main__":
    unittest.main()
