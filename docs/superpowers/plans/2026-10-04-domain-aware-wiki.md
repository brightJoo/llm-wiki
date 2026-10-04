# Domain-aware LLM Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 Wiki 엔진에 도메인별 Topic 분류 계약, 적용 범위·관계 검증, 분류 이유의 검토·게시 기능을 추가.

**Architecture:** 사람 소유 JSON 프로필과 Markdown의 JSON 메타데이터를 읽어 Topic 목록과 관계를 구성. LLM은 문서 변경과 분류 설명을 제안하고, 별도 검증 엔진이 Git 근거·구조·입력 범위를 확인한 뒤 기존 관리 PR 흐름으로 전달. 프로필이 없는 저장소는 기존 일반 Topic 흐름 유지.

**Tech Stack:** 기존 Python 표준 라이브러리, unittest, Git, Bash, GitHub Actions 유지. 기존 CI의 Python 3.12·Ubuntu 24.04 사용. 새 패키지·벡터 DB·검색 서버 없음.

**Spec:** [승인된 분류·갱신 설계](../specs/2026-10-04-llm-wiki-topic-structure.md), [문서·일별 변경 예제](../specs/2026-10-04-llm-wiki-display-example.md). [전체 구축 설계](../specs/2026-10-04-brightjoo-llm-wiki-design.md)의 첫 구현 단위에 해당.

## Global Constraints

- main 코드는 SSOT. 고정된 최종 head 기준으로 현재 구현 설명.
- API 키 없는 오픈소스 도구. 이번 단위의 설치·테스트·CI 검증에서 실제 LLM API 호출 없음.
- 실행기 adapter와 정기 실행은 다음 구현 단위. 기존 실사용 Workflow를 활성화하거나 비밀값을 등록하지 않음.
- 사람 소유 `.llm-wiki/domain.json`은 LLM이 수정하지 않음. 프로필을 지정한 저장소에서 새 분류 계약 활성화.
- 문서의 필수 항목: `schema_version`, `id`, `type`, `question`, `scope`, `lifecycle`, `relations`. schema version은 정수 `1`, lifecycle은 `active` 또는 `retired`.
- scope 값은 프로필의 ID 배열 또는 명시적 `null`. 빈 배열 금지. `null`은 전체 적용을 뜻하지 않음.
- 관계: `applies_policy`, `uses_module`, `exception_of`, `depends_on`, `related_to`. 출발·도착 유형은 프로필에서 선언.
- 기존 Topic ID·경로와 과거 로그 링크 보존. 관련된 기존 문서부터 전환. 구조 변경은 제안으로 남기고 자동 병합 없음.
- LLM 파일 편집 경로는 `docs/wiki/index.md`, `docs/wiki/log.md`, `docs/wiki/topics/**/*.md` 유지.
- 기존 한도 유지: source 200개·diff 1,000,000 bytes, Wiki 변경 30개·patch 500,000 bytes. inventory는 기존 Wiki sync의 10,000 files·50,000,000 bytes 한도를 재사용.
- 분류 설명은 별도 compiler 결과·artifact·PR 본문으로 전달. 기존 append-only log 형식은 유지.
- `coverage=incomplete`, 검증 실패, artifact 불일치이면 게시·cursor 진행 금지.
- 모든 생성·수정하는 named 함수·메서드·생성자에 정확한 docstring 또는 주석 추가. 테스트 helper·테스트 메서드·Bash 함수도 포함.

## Review Focus

1. JSON의 중복 키·boolean schema version·코드 예제 속 가짜 metadata가 정상 Topic으로 수용되지 않아야 함 → Task 1.
2. metadata 없는 기존 문서와 이전 경로가 검색 후보에서 누락되지 않아야 함 → Task 2, 3.
3. 여러 커밋 범위의 중간 삭제는 입증 가능하되 범위 밖의 가짜 삭제 근거는 거부해야 함 → Task 3.
4. 빈 Wiki patch라도 중요한 입력 변경을 읽지 못했다면 cursor가 전진하지 않아야 함 → Task 4, 5.
5. publisher의 main이 앞서거나 compiler가 profile·report를 바꿔도 입력 head·분류 설명이 바뀐 채 게시되지 않아야 함 → Task 5.

---

## 파일 책임과 공통 인터페이스

| 파일 | 책임 |
| --- | --- |
| `scripts/wiki/topic_contract.py` | 프로필·Topic metadata 파싱, 필드·유형·범위 검사 |
| `scripts/wiki/topic_inventory.py` | 기존 Wiki 목록, 코드 근거·링크·역관계, 후보 선택 |
| `scripts/wiki/source_evidence.py` | 고정 source 범위와 출처 경로의 Git 검증 |
| `scripts/wiki/classification_report.py` | compiler 결과 추출, 분류 설명 검사, 검토 본문 생성 |
| `scripts/wiki/artifact_manifest.py` | 기존 v1·분류 결과 포함 v2 artifact 계약과 checksum |
| `scripts/wiki/prepare_context.py` | 기존 context에 선택한 프로필·후보 목록 연결 |
| `scripts/wiki/validate_changes.py` | 기존 검사에 Topic·관계·분류 결과 검사를 합성 |
| `scripts/wiki/create_patch.sh`, `publish_pr.sh` | source 범위·분류 설명을 운반하고 게시 직전 재검증 |
| `.github/workflows/wiki-ingest.yml` | trusted 입력 SHA 전달, 새 검증 파일 복사, artifact 전달 |
| `CLAUDE.md`, `.llm-wiki/prompts/ingest.md`, `README.md` | 분류 절차·문서 형식·compiler 결과·설치 설명 |

새 모듈의 문제 목록은 `ContractIssue(code: str, path: str, message: str)` 사용. `topic_contract.py`에서 정의하고 기존 validator에서 `ValidationIssue`로 변환. Git·형식 오류는 `ContractError`로 명시적 실패.

새 Python 모듈은 namespace import와 직접 script 실행을 모두 지원. `__package__` 여부로 상대 import 또는 같은 trusted script 디렉터리의 import를 선택. 임의 artifact 경로를 Python import 경로에 추가하지 않음.

## Task 1: 프로필·Topic 문서 계약

**Files:** Create `scripts/wiki/topic_contract.py`, `tests/test_topic_contract.py`, `.llm-wiki/profiles/display.example.json`. 실제 `.llm-wiki/domain.json`은 자동 생성하지 않음.

**Interfaces:**
- `parse_domain_profile(content: str, path: str) -> dict[str, object]`
- `read_domain_profile(repo: Path, source_sha: str) -> Optional[dict[str, object]]`: Git snapshot의 `.llm-wiki/domain.json` 사용. 없을 때만 `None`.
- `parse_topic_metadata(content: str, path: str) -> Optional[dict[str, object]]`: metadata 절이 없는 기존 문서는 `None`. 존재하지만 잘못된 경우 오류.
- `validate_topic_metadata(metadata: dict[str, object], profile: dict[str, object], path: str) -> list[ContractIssue]`
- `ContractError`와 `ContractIssue`를 이후 task에 제공.

- [ ] **Step 1: 계약의 실패 테스트 작성.** 프로필 예제는 승인된 설계의 JSON과 동일. `test_rejects_duplicate_keys_and_bool_version`, `test_ignores_heading_inside_code_fence`, `test_rejects_multiple_metadata_blocks`, `test_legacy_topic_without_metadata`, `test_rejects_unknown_scope_and_empty_array`, `test_accepts_explicit_null_scope`, `test_profile_is_read_from_source_commit` 포함.

테스트 `setUp`에서 `self.profile`은 Demo 프로필을 파싱. `valid_metadata(self) -> dict[str, object]`는 spec의 ranking 메타데이터 새 복사본을 반환하는 test helper.

```python
def test_legacy_topic_without_metadata(self):
    """A legacy Topic remains distinguishable from malformed metadata."""
    self.assertIsNone(parse_topic_metadata("# Legacy\n\n## Scope\n\nSearch.\n", "legacy.md"))

def test_accepts_explicit_null_scope(self):
    """Unknown scope stays unknown instead of becoming a wildcard."""
    metadata = self.valid_metadata()
    metadata["scope"]["surfaces"] = None
    self.assertEqual(validate_topic_metadata(metadata, self.profile, "policy.md"), [])
    self.assertIsNone(metadata["scope"]["surfaces"])
```

- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_topic_contract.py -v` → 새 모듈 미구현 또는 계약 assertion 실패.
- [ ] **Step 3: 위 함수와 예제 프로필 구현.** 중복 JSON 키 거부, 정확한 정수 version 확인, Markdown fence 내부 heading 제외, `Topic metadata` 절의 JSON 블록 한 개만 인정. 선언된 프로필 값과 필수 차원을 검사하고 JSON 문제를 legacy fallback으로 숨기지 않음.
- [ ] **Step 4: GREEN 확인.** 같은 명령 → 모든 Task 1 테스트 통과. 예제 프로필이 실제 Wiki나 실제 회사 정책으로 표시되지 않았는지 확인.
- [ ] **Step 5: 해당 파일만 commit.** `feat: add domain and topic metadata contracts`.

## Task 2: 기존 문서 목록과 후보·관계 검색

**Files:** Create `scripts/wiki/topic_inventory.py`, `tests/test_topic_inventory.py`. Modify `scripts/wiki/prepare_context.py`, `tests/test_prepare_context.py`.

**Interfaces:** Task 1 함수 소비.
- `build_topic_inventory(repo: Path, ref: Optional[str] = None) -> list[dict[str, object]]`: path, title, metadata 또는 `None`, source 경로·SHA, 실제 Markdown Topic 링크를 담은 record. `ref=None`이면 working tree, ref가 있으면 Git tree의 Wiki snapshot.
- `select_topic_candidates(inventory: list[dict[str, object]], changes: list[dict[str, str]], profile: dict[str, object], limit: int = 20) -> dict[str, object]`: `topics`, `truncated`, `checked_count` 반환.
- `build_reverse_relations(inventory: list[dict[str, object]]) -> dict[str, list[dict[str, str]]]`: target ID → 출발 ID·관계 유형.
- 기존 context에 `domain_profile`, `topic_inventory_path`, `topic_candidates`, `topic_candidates_truncated`를 프로필 사용 시에만 추가. inventory JSON은 엔진이 runtime에 작성.

- [ ] **Step 1: 실패 테스트 작성.** `test_includes_legacy_topic_in_candidates`, `test_matches_renamed_previous_source_path`, `test_does_not_merge_matching_aliases_across_services`, `test_reverse_relations_preserve_edge_type`, `test_candidate_limit_reports_truncation`, `test_reads_seed_inventory_by_git_ref`, `test_no_profile_preserves_existing_context` 포함. legacy metadata는 `None`, rename의 이전 근거 경로는 후보에 포함, 동일 입력의 결과 순서는 같다고 assert.

```python
def test_includes_legacy_topic_in_candidates(self):
    """A cited legacy Topic remains available before migration."""
    inventory = [{"path": "docs/wiki/topics/ranking.md", "title": "Ranking", "metadata": None,
                  "sources": [{"path": "src/ranking.py", "sha": "a" * 40}], "links": []}]
    result = select_topic_candidates(inventory, [{"status": "M", "path": "src/ranking.py"}], self.profile)
    self.assertEqual(result["topics"][0]["path"], "docs/wiki/topics/ranking.md")
    self.assertIsNone(result["topics"][0]["metadata"])
```

`setUp`의 `self.profile`은 Task 1의 Demo 프로필을 파싱해 설정.
- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_topic_inventory.py -v` → 미구현 실패.
- [ ] **Step 3: inventory·후보 선택과 context 연결 구현.** 직접 source 경로 일치를 최우선으로 하며 rename 이전 경로도 포함. 이어 Topic 제목·질문·범위 별칭과 변경 경로의 토큰 일치로 후보 선택, 동점은 경로 순. 역관계는 영향 검토 후보이며 의미 동일성의 증거가 아님. 기본 후보 한도 20, 절단 여부 명시. ref 지정 시 Git blob을 읽으며 symlink Git mode도 거부. 프로필 없는 context는 기존 필드·skip 동작 유지.
- [ ] **Step 4: GREEN·회귀 확인.** `python3 -m unittest discover -s tests -p test_topic_inventory.py -v`, `python3 -m unittest discover -s tests -p test_prepare_context.py -v` → 모두 통과. inventory의 symlink·한도 초과는 읽기 실패로 처리.
- [ ] **Step 5: 해당 파일만 commit.** `feat: retrieve wiki topics using scope and source relationships`.

## Task 3: Topic 관계·수명과 일별 출처 검증

**Files:** Create `scripts/wiki/source_evidence.py`, `tests/test_source_evidence.py`. Modify `scripts/wiki/topic_contract.py`, `scripts/wiki/validate_changes.py`, `tests/test_validate_changes.py`.

**Interfaces:** Task 1·2 소비.
- `validate_source_range(repo: Path, source_base_ref: str, head_sha: str) -> tuple[str, str]`: 실제 commit 해석과 조상 관계 검사.
- `source_path_is_valid(repo: Path, path: str, head_sha: str, source_base_ref: Optional[str] = None) -> bool`: head에 존재하는 경로 또는 입증한 범위의 삭제·이동 경로.
- `validate_topic_graph(inventory: list[dict[str, object]], profile: dict[str, object], changed_paths: set[str], base_inventory: list[dict[str, object]]) -> list[ContractIssue]`
- 기존 `validate(...)` 끝에 keyword-only 선택 인자 `source_base_ref: Optional[str] = None`, `classification_path: Optional[Path] = None` 추가. CLI `--source-base-ref`, `--classification-report` 제공. 기존 인자·v1 호출 유지.

- [ ] **Step 1: 실패 테스트 작성.** `test_rejects_duplicate_topic_id`, `test_rejects_missing_or_wrong_type_relation`, `test_requires_relation_body_link`, `test_rejects_exception_cycle`, `test_preserves_untouched_legacy_topic`, `test_requires_metadata_on_changed_profiled_topic`, `test_rejects_changed_existing_id_and_topic_deletion` 포함. self-loop도 exception cycle로 거부. ID 변경·물리 삭제는 거부하며 retired 전환은 허용.
같은 test 작성 단계에 `test_accepts_path_deleted_before_last_commit`, `test_rejects_deletion_outside_source_range`, `test_rejects_non_ancestor_base`, `test_rejects_fabricated_extra_source_even_with_valid_source`, `test_accepts_indented_source_explanation` 포함.

```python
def test_accepts_path_deleted_before_last_commit(self):
    """A deletion inside the batch remains evidence at its final head."""
    self.assertTrue(source_path_is_valid(self.repo, "src/deleted.py", self.head_sha, self.source_base_sha))
    self.assertFalse(source_path_is_valid(self.repo, "src/never-existed.py", self.head_sha, self.source_base_sha))
```

이 test의 `setUp`은 임시 Git repo에 baseline `src/deleted.py` 생성 → 삭제 commit → 다른 파일 수정 commit 순서. `self.source_base_sha`는 baseline, `self.head_sha`는 마지막 commit, `self.repo`는 임시 repo 경로.

- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_source_evidence.py -v`, `python3 -m unittest discover -s tests -p test_validate_changes.py -v` → 새 범위·관계 검사 테스트 실패.
- [ ] **Step 3: 위 인터페이스와 validator 합성 구현.** profile은 artifact의 source head에서, base inventory는 Wiki diff의 seed `base_ref`에서 읽음. 변경한 profiled Topic과 참조된 ID 대상은 새 형식 필요, 무관한 legacy 문서는 일괄 전환하지 않음. 관계는 본문의 실제 상대 링크와 대조. 모든 해당 head 출처를 검사해 유효한 한 경로가 가짜 경로를 숨기지 못하게 함. source 범위 없는 기존 호출의 삭제 근거 규칙은 유지.
- [ ] **Step 4: GREEN·기존 회귀 확인.** `python3 -m unittest discover -s tests -p test_source_evidence.py -v`, `python3 -m unittest discover -s tests -p test_validate_changes.py -v` → 통과. 기존 log byte prefix·source key·허용 경로·도달성·patch 한도 검사는 그대로 통과.
- [ ] **Step 5: 해당 파일만 commit.** `feat: validate scoped topic relationships and source ranges`.

## Task 4: 분류 설명의 compiler 계약과 검토 자료

**Files:** Create `scripts/wiki/classification_report.py`, `tests/test_classification_report.py`. Modify `CLAUDE.md`, `.llm-wiki/prompts/ingest.md`.

**Interfaces:** Task 1~3 소비.
- `extract_classification(compiler_result: dict[str, object]) -> dict[str, object]`: `structured_output` 객체 또는 `result`의 정확한 JSON 문자열. fence·설명문이 섞인 JSON은 오류.
- `validate_classification(report: dict[str, object], repo: Path, source_base_ref: str, source_key: str, inventory: list[dict[str, object]], profile: dict[str, object], changed_topic_paths: set[str]) -> list[ContractIssue]`
- `render_classification(report: dict[str, object]) -> str`: PR·artifact 검토용 Markdown.
- CLI: `python3 scripts/wiki/classification_report.py --repo . --source-base-ref <sha> --source-key github:<head> --compiler-result <json> --output <classification.json>`. 엔진이 파일 작성.

- [ ] **Step 1: 실패 테스트 작성.** `test_extracts_structured_or_exact_json_result`, `test_rejects_incomplete_even_for_empty_patch`, `test_rejects_wrong_head_or_source_key`, `test_rejects_missing_decision_for_changed_topic`, `test_rejects_unaccounted_changed_source_path`, `test_proposal_cannot_change_existing_topic_boundary`, `test_renders_unknowns_and_unchanged_dependents_as_text` 포함. 실제 LLM 호출 없이 result fixture 사용.

```python
def test_rejects_incomplete_even_for_empty_patch(self):
    """An empty document patch cannot hide unread source changes."""
    self.report["coverage"] = "incomplete"
    issues = validate_classification(self.report, self.repo, self.source_base_sha, self.source_key,
                                     self.inventory, self.profile, set())
    self.assertIn("incomplete_classification", {issue.code for issue in issues})
```

`setUp`에 실제 임시 Git source 범위·Demo 프로필·빈 Wiki inventory와 그 범위에 맞는 complete report를 생성. 위 test는 coverage만 바꿈.
- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_classification_report.py -v` → 미구현·검사 실패.
- [ ] **Step 3: 추출·검증·render 구현.** report schema version `1`, source key·head는 호출자가 지정한 trusted 값과 같아야 함. `coverage=complete`만 게시 가능. decision 필드는 승인 spec대로 유지하며 `covered_changes` 경로 배열 추가, 최상위 `ignored_changes`에는 경로·지속 지식이 없는 이유를 기록. Git의 relevant 변경 경로가 둘 중 하나로 설명됐는지 검사. 이것이 업무 의미 전체 검증은 아님을 보고서에 표시.
- [ ] **Step 4: 프롬프트·Sources 예시 갱신.** 출처 설명은 SHA 다음 들여쓴 줄. 마지막 응답은 구조화 분류 설명이며 runtime 파일은 편집하지 않음. 프로필 없을 때 기존 응답·Wiki-only 흐름 유지. 고정된 최종 head, shared helper의 후처리, 미확인 범위, Topic 생성 이유를 지침에 반영.
- [ ] **Step 5: GREEN 확인.** 같은 Task 4 명령 → 모든 테스트 통과.
- [ ] **Step 6: 해당 파일만 commit.** `feat: record reviewable wiki classification decisions`.

## Task 5: artifact와 게시 직전 재검증

**Files:** Create `scripts/wiki/artifact_manifest.py`, `tests/test_artifact_manifest.py`. Modify `scripts/wiki/create_patch.sh`, `scripts/wiki/publish_pr.sh`, `.github/workflows/wiki-ingest.yml`, `tests/test_ingest_pipeline.py`.

**Interfaces:** Task 3·4 소비.
- `build_manifest(patch: bytes, base_ref: str, seed_tree: str, source_base_sha: Optional[str] = None, source_head_sha: Optional[str] = None, source_status: Optional[str] = None, classification: Optional[bytes] = None) -> dict[str, object]`
- `validate_manifest(value: dict[str, object], patch: bytes, classification: Optional[bytes] = None) -> dict[str, object]`
- `create_patch.sh <diff-base-ref> <patch-path> <metadata-path> [source-head-ref] [source-base-ref] [classification-path]`: 앞의 기존 호출 유지. 기존 네 번째 인자의 실제 compiled head 의미 유지.
- `publish_pr.sh <patch-path> <metadata-path> <base-branch> <wiki-branch> <source-key> [classification-path]`: v2에서는 env `SOURCE_BASE_SHA`도 필수, v1 기존 호출 유지.

- [ ] **Step 1: 실패 테스트 작성.** `test_v1_manifest_remains_compatible`, `test_v2_binds_classification_checksum_and_source_range`, `test_rejects_tampered_report_before_branch_mutation`, `test_incomplete_empty_patch_does_not_persist_cursor`, `test_revalidates_profile_from_compiled_head_when_main_advances`, `test_pr_body_includes_decisions_and_unknowns` 포함. publisher 테스트는 기존 fake gh·로컬 bare remote 사용. 실패 시 remote OID, cursor, PR 호출 수가 변하지 않았다고 assert.

```python
def test_v2_binds_classification_checksum_and_source_range(self):
    """Artifact verification detects changed classification bytes."""
    report = b'{"coverage":"complete"}\n'
    manifest = build_manifest(b"", "b" * 40, "absent", "a" * 40, "b" * 40, "ready", report)
    self.assertEqual(manifest["version"], 2)
    self.assertEqual(manifest["classification_sha256"], hashlib.sha256(report).hexdigest())
    with self.assertRaises(ContractError):
        validate_manifest(manifest, b"", b"changed")
```

manifest 순수 함수는 bytes 일치 검사를 담당. report의 의미·전체 계약 검사는 Task 4와 publisher에서 별도 수행.
- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_artifact_manifest.py -v`, Ubuntu에서 `python3 -m unittest discover -s tests -p test_ingest_pipeline.py -v` → 새 artifact·publisher 기능 실패.
- [ ] **Step 3: manifest·shell 계약 구현.** source head에 프로필이 없으면 v1의 exact key 집합 유지. 프로필이 있으면 v2이며 source base를 명시하지 않은 생성 호출은 오류. v2는 기존 필드와 `source_base_sha`, `source_head_sha`, `source_status`, `classification_sha256` 추가. source status는 `ready`, `no_changes`, `docs_wiki_only`; `ready`면 classification 필수, skip이면 report hash는 `null`이고 Wiki patch는 비어야 함. source status는 trusted Git 범위로 재계산. source head는 source key와, source base는 trusted `SOURCE_BASE_SHA`와 일치. checksum은 bytes 기준.
- [ ] **Step 4: Workflow 연결 구현.** context 단계에서 compiler 실행 전에 실제 base/head를 step output으로 고정해 publish job까지 전달. 원본 context를 compiler 뒤에 신뢰해서 SHA를 다시 정하지 않음. pristine validation에서 같은 bound source 범위·결과를 검증하고 artifact로 함께 전달. trusted publisher 디렉터리에 새 Python 모듈 전체를 복사. 프로필이 켜진 `ready` 입력의 v1 artifact downgrade는 거부. profile·검증 코드·PR 본문은 artifact의 실행 코드에서 가져오지 않음.
- [ ] **Step 5: GREEN·publisher 회귀 확인.** 앞의 Task 5 명령 모두 통과. 기존 미병합 PR 누적·unmanaged branch 거부·seed hash 불일치·empty cursor·main 전진 테스트도 통과. PR body는 body-file로 생성하며 runtime의 텍스트를 shell 명령으로 해석하지 않음.
- [ ] **Step 6: 해당 파일만 commit.** `feat: publish verified wiki classification artifacts`.

## Task 6: 합성 회귀 자료·사용 문서·전체 검증

**Files:** Create `tests/fixtures/wiki_classification/cases.json`, `tests/fixtures/wiki_classification/acceptance.md`, `tests/test_domain_pipeline.py`. Modify `README.md`, 필요한 기존 test fixture.

**Interfaces:** 기존 compiler fake를 Task 4 계약으로 확장. `cases.json` schema version `1`, 사례별 id·설명·기대 Topic·금지 범위 확장·필수 근거·허용 대안을 담음. 실제 비공개 코드·회사 정책 없음.

- [ ] **Step 1: 12개 사례와 실패 테스트 작성.** spec의 공통 조건·랭킹 예외·같은 helper 다른 후처리·서비스 차이·연속 변경/원복·모듈 계약·이동·삭제·외부 설정 미확인·기존 후보·실패 재시도·legacy 전환을 각각 식별. `test_profiled_batch_pipeline_uses_final_head_and_accumulates_pending_wiki`, `test_profiled_no_durable_change_requires_complete_report`, `test_profile_change_is_not_trusted_from_compiler_worktree` 포함. 범위 결정은 fake의 기대 입력·출력으로 계약을 검사하며 이를 LLM 의미 정확도 평가로 주장하지 않음.

```python
def test_profiled_batch_pipeline_uses_final_head_and_accumulates_pending_wiki(self):
    """The pipeline binds a batch and pending Wiki to the final source head."""
    result = self.compile_and_publish_fake_batch()
    self.assertEqual(result["report"]["head_sha"], result["head_sha"])
    self.assertEqual(result["managed_cursor"], result["head_sha"])
    self.assertEqual(result["pending_pr_count"], 1)
    self.assertIn("7일", result["wiki"]["ranking-sold-out"])
    self.assertNotIn("3일", result["wiki"]["ranking-sold-out"])
```

`compile_and_publish_fake_batch(self) -> dict[str, object]`는 이 test 파일의 helper: 승인 예제의 C1~C4 합성 commit, 기존 pending Wiki seed, fake compiler·fake gh로 실제 엔진을 호출. 반환 필드는 `report` 객체, `head_sha` 문자열, `managed_cursor` 문자열, `pending_pr_count` 정수, `wiki` 문서명→본문 map, 진단용 `calls` 배열. Wiki 본문과 report는 fake 결과이며 실제 LLM 판단으로 표시하지 않음.
- [ ] **Step 2: RED 확인.** `python3 -m unittest discover -s tests -p test_domain_pipeline.py -v` → 필요한 연결 또는 fixture가 없으면 실패.
- [ ] **Step 3: fixture·설치·복구 문서 반영.** 예제 프로필 선택·Git에 기록하는 방법, metadata 없는 문서 전환, 보고서·미확인 상태 해석, validator CLI, retired 처리 안내. 부분 문서화를 전체 서비스 설명으로 표시하지 않음. 실제 실행기 연결과 12개 사례의 사람 의미 평가 결과는 미측정으로 명시. API 키 없는 로컬 검사·CI를 실사용 LLM 생성과 구분.
- [ ] **Step 4: 전체 검증.** Ubuntu 환경에서 아래 명령 모두 exit 0. 현재 Windows에는 Linux 배포판·shellcheck·Go를 확인하지 못했으므로 shell·publisher 전체 검증은 Ubuntu CI 또는 준비된 Linux 환경에서 수행. 일부 Python 검사만 실행하고 전체 통과로 보고하지 않음.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
shellcheck scripts/wiki/*.sh
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.9
git diff --check 77c550e...HEAD
```

- [ ] **Step 5: 검증 결과·한계 기록.** 초기 자료에서 사람이 확인할 의미 항목은 `acceptance.md`에 남김. 실제 모델 실행 없이 품질 수치·정책 정확도를 주장하지 않음.
- [ ] **Step 6: 해당 파일만 commit.** `test: cover domain-aware wiki ingestion and document its limits`.

## 구현 완료와 전달

- [ ] 승인된 spec의 문서 계약·단계·실패 처리마다 구현 task와 테스트를 대응 확인.
- [ ] 전체 branch를 새 시각으로 검토하고 발견한 문제 수정. 직접 구현 방식을 선택하면 마지막에 독립 reviewer 한 명, 분담 방식을 선택하면 task별 구현·검토와 전체 검토.
- [ ] 실제 실행한 검증·하지 못한 실제 LLM 평가·후속 adapter/schedule 범위를 구분해 결과 보고.
- [ ] GitHub 인증과 소유 저장소를 확인한 뒤 feature branch와 검토 PR로 전달. 자동 merge와 Workflow 활성화는 수행하지 않음. 생성한 PR은 Codex task에 연결.

## 계획 검토 상태

자체 검토: 프로필 없는 호환 흐름, legacy의 점진 전환, 안정 ID·경로, source 범위, report의 빈 patch 처리, publish 직전 이중 검사, PR body, 합성 평가·의미 평가 구분을 각 task에 배치. 일별 schedule과 key-free 실행기 adapter는 다음 별도 계획으로 유지.

사용자가 설계·계획과 직접 구현 후 독립 전체 검토 방식을 승인. Tasks 1~5 구현·검사 완료, Task 6의 합성 pipeline·문서 구현 후 전체 검증 진행 중. 실행기 adapter·일별 schedule은 이 계획의 후속 단위.
