"""Milestone 2 Empirical Stress Test Suite: Concurrency, Lifecycle & WAL Challenger.

Tests:
1. Multi-Scope Dynamic Concurrency Stress: 24 dynamic scopes, concurrent init/ingest/search/admin, zero path traversal, directory containment.
2. Single-Scope Concurrent Acquisition Race: 30 concurrent tasks acquiring same new scope simultaneously, double-checked locking singleton verification.
3. Lifecycle & Termination During Active Background Ingestion: plugin.terminate() while 30 background ingestion tasks are writing to SQLite.
4. Lifecycle Force Cancellation Timeout Protection: Slow/hanging task, terminate() 5.0s timeout and cancel enforcement.
5. Concurrent Idempotent Termination: 5 simultaneous calls to terminate().
"""

from __future__ import annotations

import asyncio
import gc
import logging
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Setup sys.path to allow running standalone or via pytest
plugin_dir = Path(__file__).resolve().parent.parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))
if str(plugin_dir.parent) not in sys.path:
    sys.path.insert(0, str(plugin_dir.parent))

# Install test stub if running standalone
from tests.conftest import _install_astrbot_stub  # noqa: E402

_install_astrbot_stub()

from astrbot_plugin_memorix.memorix.engine import ScopeEngineManager  # noqa: E402
from astrbot_plugin_memorix.tests.mocks import (  # noqa: E402
    MockAstrMessageEvent,
    create_test_plugin_instance,
)

logger = logging.getLogger("test_m2_stress")


def _check_db_clean_and_unlocked(db_path: Path) -> None:
    """Verify SQLite database on disk: integrity check passes and no locks remain."""
    assert db_path.exists(), f"Database file does not exist: {db_path}"
    conn = sqlite3.connect(str(db_path), timeout=2.0)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        assert rows == [("ok",)], f"Integrity check failed for {db_path}: {rows}"
        # Check WAL status
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        wal_res = cursor.fetchall()
        logger.info("WAL checkpoint for %s: %s", db_path.name, wal_res)
    finally:
        conn.close()


def test_multiscope_dynamic_concurrency_stress():
    """Suite 1: Multi-Scope Extreme Dynamic Concurrency Stress (24 distinct dynamic scopes, concurrent init/ingest/search/admin)."""
    async def _run():
        temp_dir = Path(tempfile.mkdtemp(prefix="memorix_scope_stress_"))
        try:
            manager = ScopeEngineManager(
                base_data_dir=temp_dir,
                config={
                    "embedding": {"provider": "builtin_hash"},
                },
            )

            # 24 distinct dynamic scopes: normal groups, user DMs, and adversarial injection attempts
            scopes = [
                f"dynamic_group_{i:03d}" for i in range(16)
            ] + [
                f"user_direct_{j:03d}" for j in range(4)
            ] + [
                "nested/scope/traversal/../../attack_scope",
                "COM1",  # Windows reserved device name
                "special!@#$%^&*()_+={}|[]~`",
                "super_long_scope_" + ("x" * 100),
            ]

            total_concurrency = len(scopes)  # 24 concurrent distinct scopes
            errors = []

            async def _worker(task_id: int, scope_key: str):
                try:
                    # 1. Acquire / Initialize engine concurrently across distinct scopes
                    engine = await manager.get_engine(scope_key)
                    assert engine is not None
                    assert engine.is_initialized

                    # 2. Concurrent message ingestion
                    sender = f"user_{task_id}"
                    content = f"Stress message from task {task_id} in scope {scope_key} at {time.time_ns()}"
                    ingest_res = await engine.ingest_message(
                        sender_id=sender,
                        sender_name=f"Sender_{sender}",
                        content=content,
                        chat_id=f"chat_{task_id}",
                    )
                    assert ingest_res.success, f"Ingest failed: {ingest_res.error}"

                    # 3. Concurrent memory search
                    search_res = await engine.search("Stress message", limit=3)
                    assert isinstance(search_res, list)

                    # 4. Concurrent admin dispatch (using valid runtime component)
                    admin_res = await engine.dispatch_admin("memory_runtime_admin", "self_check")
                    assert isinstance(admin_res, dict)

                    return (task_id, scope_key, True)
                except Exception as exc:
                    errors.append((task_id, scope_key, exc))
                    return (task_id, scope_key, False)

            start_time = time.perf_counter()
            tasks = [
                asyncio.create_task(_worker(i, scopes[i]))
                for i in range(total_concurrency)
            ]
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start_time

            # Assert zero errors across all concurrent distinct scope operations
            assert len(errors) == 0, f"Concurrent tasks failed with errors: {errors}"
            assert all(r[2] for r in results)

            # Verify physical directory containment (Zero Path Traversal)
            scopes_dir = temp_dir / "scopes"
            assert scopes_dir.exists()
            for child in scopes_dir.iterdir():
                # Ensure all created subdirectories are strictly inside scopes_dir
                assert str(child.resolve()).startswith(str(scopes_dir.resolve())), f"Path traversal detected: {child}"
                # Check metadata.db existence and integrity
                db_file = child / "metadata.db"
                if db_file.exists():
                    _check_db_clean_and_unlocked(db_file)

            # Verify safe shutdown of all engines
            active_before = manager.list_active_scopes()
            assert len(active_before) == total_concurrency
            await manager.shutdown_all()
            assert len(manager.list_active_scopes()) == 0

            print(f"  [PASS] Multi-Scope Concurrency Stress: {total_concurrency} distinct scopes concurrent init/ingest/search/admin in {elapsed:.3f}s (0 errors)")
        finally:
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_concurrent_single_scope_race_condition():
    """Suite 2: Double-Checked Locking Race Condition on Single Scope Acquisition."""
    async def _run():
        temp_dir = Path(tempfile.mkdtemp(prefix="memorix_single_race_"))
        try:
            manager = ScopeEngineManager(base_data_dir=temp_dir)
            target_scope = "high_contention_group_8888"
            concurrency = 30

            engines = []
            errors = []

            async def _acquire(task_id: int):
                try:
                    eng = await manager.get_engine(target_scope)
                    engines.append(eng)
                except Exception as exc:
                    errors.append(exc)

            start_time = time.perf_counter()
            await asyncio.gather(*[_acquire(i) for i in range(concurrency)])
            elapsed = time.perf_counter() - start_time

            assert len(errors) == 0, f"Race condition errors: {errors}"
            assert len(engines) == concurrency
            # All 30 callers MUST receive the exact same singleton instance
            first_id = id(engines[0])
            assert all(id(e) == first_id for e in engines), "Double-checked locking failure: Multiple engine instances created!"

            await manager.shutdown_all()
            print(f"  [PASS] Single-Scope Acquisition Race: {concurrency} tasks converged to singleton in {elapsed:.3f}s")
        finally:
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_plugin_terminate_during_active_ingestion_stress():
    """Suite 3: Lifecycle Termination Stress while Background Ingestions are actively writing."""
    async def _run():
        temp_dir = Path(tempfile.mkdtemp(prefix="memorix_lifecycle_active_"))
        try:
            plugin, mock_context = create_test_plugin_instance(temp_dir)
            await plugin.initialize()

            # Generate 30 messages across 5 distinct groups/users
            num_messages = 30
            events = []
            for i in range(num_messages):
                ev = MockAstrMessageEvent(
                    message_str=f"Ingestion message payload #{i}: Alice and Bob discussed quantum computing at {time.time_ns()}",
                    sender_id=f"user_{i % 5}",
                    sender_name=f"UserName_{i % 5}",
                    group_id=f"group_{i % 3}",
                    platform_name="aiocqhttp",
                )
                events.append(ev)

            # Dispatch messages concurrently into plugin
            for ev in events:
                await plugin.on_all_messages(ev)

            # Verify that background tasks were spawned
            assert len(plugin._background_tasks) > 0, "No background tasks spawned!"
            initial_spawned = len(plugin._background_tasks)

            # Sleep briefly (20ms) to ensure coroutines enter execution & SQLite transactions
            await asyncio.sleep(0.02)

            # Trigger plugin.terminate() while background tasks are actively running!
            term_start = time.perf_counter()
            await plugin.terminate()
            term_elapsed = time.perf_counter() - term_start

            # 1. Must terminate gracefully within the 5.0s SLA
            assert term_elapsed < 5.0, f"terminate() took too long: {term_elapsed:.3f}s (exceeded 5.0s SLA)"

            # 2. All background tasks must be done / discarded
            pending_after = [t for t in plugin._background_tasks if not t.done()]
            assert len(pending_after) == 0, f"Lingering background tasks after terminate: {pending_after}"

            # 3. Verify SQLite files on disk: databases must be intact and fully unlocked
            scopes_dir = temp_dir / "memorix_data" / "scopes"
            if scopes_dir.exists():
                for sc in scopes_dir.iterdir():
                    db_path = sc / "metadata.db"
                    if db_path.exists():
                        _check_db_clean_and_unlocked(db_path)

            # 4. Verify directory can be deleted immediately without Windows PermissionError (File Handle Leak check)
            gc.collect()
            shutil.rmtree(temp_dir)
            assert not temp_dir.exists(), "Directory still exists after rmtree, handle lock leak!"

            print(f"  [PASS] Termination Under Active Ingestion: {initial_spawned} background tasks stopped cleanly in {term_elapsed:.3f}s; WAL & handles verified")
        finally:
            gc.collect()
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_plugin_terminate_force_cancellation_timeout_stress():
    """Suite 4: Lifecycle Force Cancellation Timeout: slow/hanging task cancelled within 5.0s."""
    async def _run():
        temp_dir = Path(tempfile.mkdtemp(prefix="memorix_lifecycle_timeout_"))
        try:
            plugin, mock_context = create_test_plugin_instance(temp_dir)
            await plugin.initialize()

            # Inject an intentionally hanging background coroutine (simulating a deadlocked I/O or network hang)
            cancelled_flag = False

            async def _hanging_task():
                nonlocal cancelled_flag
                try:
                    await asyncio.sleep(60.0)
                except asyncio.CancelledError:
                    cancelled_flag = True
                    raise

            hanging_task_ref = plugin._spawn_background_task(_hanging_task())
            assert hanging_task_ref in plugin._background_tasks

            # Call terminate(): should wait 5.0s, hit timeout, cancel hanging task, and exit cleanly
            term_start = time.perf_counter()
            await plugin.terminate()
            term_elapsed = time.perf_counter() - term_start

            # Assert timing is governed by the 5.0s timeout (5.0s <= elapsed < 6.5s)
            assert 4.8 <= term_elapsed <= 6.5, f"terminate() elapsed time unexpected: {term_elapsed:.3f}s (expected ~5.0s timeout)"
            assert hanging_task_ref.cancelled() or hanging_task_ref.done()
            assert cancelled_flag, "Hanging background task was not cancelled properly!"

            print(f"  [PASS] Force Cancellation Timeout: Hanging task cancelled within {term_elapsed:.3f}s (5.0s timeout enforced)")
        finally:
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_plugin_concurrent_idempotent_terminate():
    """Suite 5: Concurrent Idempotent Termination: 5 simultaneous calls to terminate()."""
    async def _run():
        temp_dir = Path(tempfile.mkdtemp(prefix="memorix_idempotent_term_"))
        try:
            plugin, mock_context = create_test_plugin_instance(temp_dir)
            await plugin.initialize()

            # Launch 5 concurrent calls to terminate()
            start_time = time.perf_counter()
            results = await asyncio.gather(*[plugin.terminate() for _ in range(5)], return_exceptions=True)
            elapsed = time.perf_counter() - start_time

            errors = [r for r in results if isinstance(r, Exception)]
            assert len(errors) == 0, f"Concurrent terminate errors: {errors}"

            print(f"  [PASS] Concurrent Idempotent Termination: 5 simultaneous terminate() calls succeeded in {elapsed:.3f}s")
        finally:
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    asyncio.run(_run())


def test_ast_zero_unhandled_astrbot_core_imports():
    """Suite 6: AST Static Analysis: zero unhandled astrbot.core imports across all target files."""
    import ast
    target_files = [
        plugin_dir / "main.py",
        plugin_dir / "memorix" / "app_context.py",
        plugin_dir / "memorix" / "memory_injection.py",
        plugin_dir / "memorix" / "utils" / "message_formatting.py",
        plugin_dir / "memorix" / "services" / "container.py",
    ]

    class ImportVisitor(ast.NodeVisitor):
        def __init__(self):
            self.try_depth = 0
            self.violations = []

        def visit_Try(self, node):
            self.try_depth += 1
            self.generic_visit(node)
            self.try_depth -= 1

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name.startswith("astrbot.core") and self.try_depth == 0:
                    self.violations.append((node.lineno, alias.name))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if (node.module or "").startswith("astrbot.core") and self.try_depth == 0:
                self.violations.append((node.lineno, node.module))
            self.generic_visit(node)

    all_violations = []
    for f in target_files:
        if not f.exists():
            continue
        visitor = ImportVisitor()
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        visitor.visit(tree)
        for lineno, mod in visitor.violations:
            all_violations.append(f"{f.name}:{lineno} -> {mod}")

    assert len(all_violations) == 0, f"Found unhandled astrbot.core imports: {all_violations}"
    print(f"  [PASS] AST Static Analysis: 0 unhandled astrbot.core imports across {len(target_files)} files")


def main():
    print("=" * 70)
    print("  MEMORIX M2 EMPIRICAL CONCURRENCY & LIFECYCLE STRESS TEST SUITE")
    print("=" * 70)
    test_multiscope_dynamic_concurrency_stress()
    test_concurrent_single_scope_race_condition()
    test_plugin_terminate_during_active_ingestion_stress()
    test_plugin_terminate_force_cancellation_timeout_stress()
    test_plugin_concurrent_idempotent_terminate()
    test_ast_zero_unhandled_astrbot_core_imports()
    print("=" * 70)
    print("  ALL 6 EMPIRICAL STRESS TEST SUITES COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()

