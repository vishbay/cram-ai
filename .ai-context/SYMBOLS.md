cram/add_context.py: add_files, main
cram/audit.py: ratio_band, collect_audit, run_audit, run_report, run_report_html, run_compare, run_session, format_layer_row, collect_layer, run_layer, main
cram/audit_events.py: Event, SessionMeta, repo_rel, parse_claude, parse_cursor_jsonl, parse_cursor_db, parse_codex, derive_session, derive_session_timeline
cram/audit_findings.py: and, derive_findings
cram/audit_report.py: render_report
cram/audit_report_html.py: render_report_html
cram/audit_store.py: resolve_db_path, AuditStore
cram/benchmark.py: run_benchmark, main
cram/cli.py: main
cram/context_dir.py: canonical_context_dir, legacy_context_dir, resolve_context_dir, has_context_dir, context_path, context_basename
cram/cost_model.py: resolve_provider, get_provider_pricing, CostInputs, orientation_tokens, budget_status, daily_costs
cram/decide.py: append_decision, main
cram/decisions.py: show_decisions, mine_decisions, main
cram/doctor.py: main
cram/find_context.py: find_relevant_files, populate_current_task, find_context, main
cram/gotcha.py: append_gotcha, main
cram/health.py: context_health
cram/hooks.py: install_global_claude_md, uninstall_global_claude_md, install_commit_msg_hook, install_hook, main, install_claude_code_hooks, uninstall_hook
cram/init.py: scan_structure, generate_architecture_md, write_gitignore, write_ci_action, init_repo, main
cram/mcp_server.py: get_context, get_architecture, get_symbols, get_decisions, propose_decision, get_gotchas, get_health, add_file, run_benchmark, get_task_history, main
cram/recommend.py: Optimizer, waste_class_for, recommend_for, attach_recommendations
cram/rig.py: Task, load_corpus, Availability, ProviderAdapter, BaselineAdapter, CramAdapter, HeadroomAdapter, ContextModeAdapter, ClaudeContextAdapter, get_provider, Runner, LiveRunner, CodexRunner, MockRunner, Oracle, CommandOracle, effective_tokens, optimizer_active, observe_optimizer, render_observation, RunResult, run_rig, summarize, render_summary, main
cram/session.py: save_session, set_last_slot, get_last_slot, archive_task, load_session, touch_session, session_age, session_within_grace, clear_session
cram/status.py: staleness_score, staleness_band, get_status_dict, show_status, main
cram/suggest.py: suggest_task
cram/symbols.py: extract_symbols, write_symbols_md
cram/sync_context.py: get_git_diff, update_architecture_md, reset_task, sync, main
cram/targets.py: load_custom_targets, get_effective_targets, get_effective_indicators, load_output_config, load_default_target, save_default_target, detect_targets, write_to_target, write_to_all_detected
cram/usage.py: measured_usage
cram/utils.py: load_settings, save_settings, discover_models, pick_context_model, pick_coding_model, cache_min_tokens, get_model_recommendations, call_context_model, call_model, find_git_root, strip_code_fence
examples/rig/fixtures/add-cli-flag/app.py: main
examples/rig/fixtures/add-cli-flag/test_app.py: test_result_always_printed, test_quiet_has_no_debug, test_verbose_emits_debug
examples/rig/fixtures/fix-failing-test/stats.py: mean, median
examples/rig/fixtures/fix-failing-test/test_stats.py: test_mean, test_median_odd, test_median_even
tests/test_audit.py: TestAnalyzeTranscript, TestRatioBand, TestCollectAudit, TestContextBloat, TestRetryLoops, TestRunCompare, TestFindAllToolUse, TestAuditConstants, TestCursorTranscript, TestCursorWorkspaceDb, TestCollectAuditWithCursor, TestCodexTranscript, TestCollectAuditWithCodex
tests/test_audit_drilldown.py: TestPerSessionFileCounts, TestTopReadFiles
tests/test_audit_findings.py: TestRules, TestEndToEnd
tests/test_audit_layers.py: TestLayerRows, TestRunLayer
tests/test_audit_measured.py: TestPerSessionMeasured, TestAggregateMeasured, TestCodexRelativePatchPaths
tests/test_audit_report.py: TestRenderReport, TestSessionIdent, TestRunReport
tests/test_audit_report_html.py: TestRenderReportHtml, TestRunReportHtml
tests/test_audit_store.py: TestResolveDbPath, TestRoundtrip, TestLedger, TestInvalidation, TestCursorDbSessions, TestIncrementalCollect, TestParseFailureSurfacing, TestContextModeDetection, TestContextModeSegment, TestIngestProgress
tests/test_audit_timeline.py: TestTimelineRows, TestConsecutiveUsageCollapse, TestWasteAttribution, TestRunSessionCLI
tests/test_cli.py: TestVersionFlag, TestUsage
tests/test_context_dir.py: test_prefers_canonical_context_dir, test_falls_back_to_legacy_context_dir, test_context_path_uses_resolved_dir
tests/test_cost_model.py: test_orientation_caps_at_repo_tokens, test_orientation_zero_files, test_daily_saving_never_negative, test_nocram_scales_linearly_with_orient_files, test_daily_costs_returns_expected_keys, test_import_works, TestBudgetStatus, TestProviderPricing, TestResolveProvider, TestEnterpriseProviderPricing
tests/test_decisions.py: repo, TestFilterCommits, TestParseModelOutput, TestAppendWithReason, TestShowDecisions, TestMineDecisions
tests/test_find_context.py: TestCleanPath, TestReadTruncated, TestFindRelevantFiles, TestPopulateCurrentTask, TestScoreFiles, TestContractFields, TestFindContext, TestChdirFreeExtraction, TestResolvePathDisambiguation
tests/test_hooks.py: TestInstallCommitMsgHook, TestInstallHookInstallsBoth, TestCommitMsgPatternDetection
tests/test_init.py: TestIsExcludedFile, TestScanStructure, TestWriteGitignore, TestInitRepo
tests/test_mcp_server.py: repo, TestGetArchitectureDeterminism, TestGetDecisionsDeterminism, TestGetSymbolsDeterminism, TestGetContextDeterminism, TestGetHealthDeterminism, TestTaskSlotNamespacing, TestUsageLog, TestProposeDecision, TestSlotCoherence, TestArchiveTask, TestInitGuard
tests/test_recommend.py: TestRegistryIntegrity, TestLookups, TestDetectors, TestAttach, TestDeriveFindingsCarriesRecommendations
tests/test_rig.py: TestCorpus, TestProviders, TestCommandOracle, TestEffectiveTokens, TestRunRig, TestLiveRunner, TestCodexRunner, TestClaudeContextAdapter, TestDetector, TestObserve
tests/test_rig_fixtures.py: mean, median, main, test_corpus_loads_and_fixtures_exist, test_solver_passes_the_oracle, test_noop_fails_the_oracle_because_fixtures_ship_red, test_summary_separates_solver_from_noop
tests/test_status.py: TestStalenessScore, TestStalenessBand, TestGetStatusDictBackCompat, git_repo, TestGetStatusDictIntegration, TestStructureHashFreshness
tests/test_symbols.py: TestByteStability
tests/test_sync.py: TestGetGitDiff, TestUpdateArchitectureMd, TestSync
tests/test_targets.py: TestUpsertCramSectionRegexSafe, TestSaveLoadDefaultTarget, TestDetectTargets, TestWriteToTarget, TestCustomTargets
tests/test_usage.py: test_missing_dir_returns_none, test_sums_match_fixture, test_malformed_lines_skipped, test_old_files_excluded
tests/test_utils.py: TestStripCodeFence, TestCallModelRouting, TestCallViaLitellmMissing, TestProxyHeaders, TestProbeLmStudio, TestCallViaOpenaiCompat, TestCallViaGemini, TestCallContextModelRouting, TestDiscoverModelsLmStudio, TestPickContextModel