"""Temporary, read-only source inspection for the approved PR migration."""
import ast
from pathlib import Path

specs = {
'apps/api/app/services/product_service.py': ['_judge_spend_control','_judge_spend_path','_reserve_judge_credits','_refund_judge_credits','_judge_model_name','_judge_spend_state','_reset_judge_spend_for_tests'],
'apps/api/app/services/execution_run_store.py': ['record_judge_review','deterministic_evaluation_snapshot','apply_judge_review'],
'apps/api/app/services/benchmark_service.py': ['get_scenario_contract','run_scenario'],
'apps/api/app/schemas/execution.py': ['ConversationRecord','ExecutionRunRecord','ExecutionRunProgress'],
'apps/api/app/schemas/product.py': ['JudgeRequest','JudgeResponse','ProductConfig'],
'apps/api/tests/test_execution_runs.py': ['test_confirmed_llm_adjudication_updates_effective_evaluation_and_preserves_automatic_result'],
'apps/api/tests/test_openai_codex_oauth.py': ['test_llm_judge_blocks_without_provider_and_runs_when_connected'],
'apps/api/tests/test_product.py': ['test_llm_judge_is_gated_without_provider_regardless_of_plan','test_llm_judge_spend_control_respects_budget_env','test_product_audit_events_track_saved_runs_exports_and_judge_requests'],
'apps/api/tests/test_assert_judge_audit.py': [],
'apps/api/tests/test_upstream_assert_judge.py': [],
'apps/api/tests/test_assert_judge_provenance.py': [],
'apps/api/tests/test_assert_review_status.py': [],
'apps/api/app/services/execution_runner.py': [],
'apps/api/app/integrations/assert_runtime.py': ['judge_score_contract','installed_version'],
}
for file, names in specs.items():
    p=Path(file); source=p.read_text(); lines=source.splitlines(); tree=ast.parse(source)
    print('\n### FILE', file)
    print('TOP_LEVEL:', ', '.join(f'{n.name}:{n.lineno}-{n.end_lineno}' for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))))
    for node in tree.body:
        if getattr(node,'name',None) in names:
            print('\nDEFINITION',node.name)
            print('\n'.join(f'{i+1}: {lines[i]}' for i in range(node.lineno-1,min(node.end_lineno,node.lineno+149))))
    if file.endswith(('execution_runner.py','benchmark_service.py')):
        for i,line in enumerate(lines):
            if 'evaluation_findings=' in line or "'scenario_contract':" in line or "'scenario_contract_sha256':" in line or "'evaluation_findings':" in line:
                print('CONTRACT/EVIDENCE CONTEXT:', '\n'.join(f'{j+1}: {lines[j]}' for j in range(max(0,i-3),min(len(lines),i+8))))
    if '/tests/test_assert_' in file:
        print('\nTEST SETUP:', '\n'.join(lines[:80]))
for file in ['apps/web/components/BenchmarkRunner.tsx','apps/web/components/RunDetailPage.tsx','apps/web/lib/execution.ts']:
    lines=Path(file).read_text().splitlines(); printed=set()
    print('\n### FRONTEND', file)
    for i,line in enumerate(lines):
        if any(term in line for term in ['interface ProductConfig','interface JudgeGate','requestJudge(', 'judgeProviderReady','onClick={() => void onJudge', 'canJudge =','requestLlmJudge(', 'LLM judge</p>','Connect OpenAI for the local','llm_judge_status']):
            for j in range(max(0,i-3),min(len(lines),i+22)):
                if j not in printed: print(f'{j+1}: {lines[j]}'); printed.add(j)
print('\nSOURCE_INSPECTION_COMPLETE: no source or reference changes made.')
raise SystemExit(1)
