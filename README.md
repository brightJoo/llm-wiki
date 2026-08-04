# LLM Wiki

운영 중인 코드베이스의 지식을 GitHub의 변경 이력에서 점진적으로 축적하는 repository-local LLM Wiki toolkit입니다.

이 프로젝트는 기존 서비스를 처음부터 다시 문서화하지 않습니다. 오늘 이후 `main`에 병합되는 코드 변경부터 현재 코드베이스와 대조하여 Markdown Wiki로 컴파일합니다.

## 왜 만드는가

오래 운영된 서비스에는 코드, Spec, LLD, Pull Request, 운영 경험이 서로 다른 곳에 흩어져 있습니다. 문서를 한 번에 전부 만들기는 어렵고, 만들어진 문서도 코드 변경을 따라가지 못하면 빠르게 낡습니다.

LLM Wiki는 모든 질문마다 원본 자료를 다시 읽는 대신, 확인된 지식을 지속적으로 갱신되는 문서로 만들어 둡니다. 읽을 때는 이미 정리된 작은 문서 집합을 사용하고, 쓸 때만 코드와 관련 자료를 깊게 확인합니다.

이 접근은 Andrej Karpathy가 제안한 [LLM Wiki 패턴](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)을 운영 중인 소프트웨어 저장소에 적용한 것입니다.

## 핵심 원칙

- **점진적으로 쌓는다.** 과거 전체를 선행 문서화하지 않고, 변경되거나 필요해진 지식부터 축적합니다.
- **쓰기 전에 검증한다.** Wiki에 기록하기 전에 항상 현재 `main`의 코드, 테스트, Spec과 대조합니다.
- **기존 문서를 먼저 찾는다.** 관련 문서가 있으면 갱신하고, 독립적인 개념일 때만 새 문서를 만듭니다.
- **Spec과 사실을 구분한다.** Spec·LLD는 의도이고 코드는 현재 구현입니다. 서로 다르면 한쪽을 조용히 덮어쓰지 않고 괴리를 드러냅니다.
- **변경은 Pull Request로 제안한다.** LLM이 생성한 지식은 사람이 검토할 수 있도록 문서 PR로 제출합니다.
- **읽기는 작게, 쓰기는 선택적으로 한다.** `index.md`에서 관련 문서를 찾은 뒤 필요한 코드와 문서만 읽습니다.

## 지식이 들어오는 경로

### `main`에 병합된 코드

GitHub Actions가 새로 병합된 변경 범위를 확인합니다. LLM은 관련 Wiki 페이지와 Spec·LLD를 찾고, 현재 구현에 맞게 필요한 문서만 생성하거나 갱신합니다.

```text
main merge
  -> 변경된 코드와 테스트 확인
  -> 관련 Wiki 및 Spec 탐색
  -> 기존 문서 갱신 또는 새 Topic 생성
  -> index.md와 log.md 갱신
  -> Wiki 변경 PR 생성
```

`wiki-ingest.yml`은 이 흐름을 두 job으로 나눕니다.

| job | 권한과 책임 |
| --- | --- |
| `ingest` | 저장소 읽기 권한과 Anthropic API key를 사용해 context를 만들고 `claude -p`를 실행합니다. Claude 실행 후 만든 별도 pristine checkout의 엔진으로 Wiki patch를 검증합니다. GitHub에 쓸 수 없습니다. |
| `publish-pr` | Anthropic API key 없이 patch를 임시 worktree에서 먼저 검증하고, 통과한 경우에만 `wiki/pending` branch와 PR을 생성하거나 갱신합니다. |

Claude는 GitHub 쓰기 권한을 가지지 않으며, publish job은 Claude를 실행하지 않습니다. 두 job 사이에는 Wiki patch와 검증 metadata만 artifact로 전달됩니다.

연속해서 `main`이 변경되면 엔진이 관리하는 `wiki/pending` branch의 Wiki를 다음 실행의 seed로 사용합니다. 따라서 미병합 지식도 잃지 않고 하나의 PR에 누적됩니다. 문서 변경이 없는 실행도 branch의 commit trailer에 처리한 source cursor를 남기므로 같은 범위를 반복해서 읽거나 중간 merge를 놓치지 않습니다. 사용자 소유 branch나 marker가 없는 PR은 덮어쓰지 않습니다.

## LLM Wiki 구조

```text
.
├── CLAUDE.md
├── .github/
│   └── workflows/
│       ├── wiki-ingest.yml
│       └── ci.yml
├── .llm-wiki/
│   └── prompts/
│       └── ingest.md
├── scripts/
│   └── wiki/
│       ├── prepare_context.py
│       ├── run_compiler.sh
│       ├── sync_wiki.py
│       ├── validate_changes.py
│       ├── create_patch.sh
│       └── publish_pr.sh
└── docs/
    ├── specs/
    │   └── <feature>/
    │       ├── spec.md
    │       ├── lld.md
    │       └── acceptance.md
    └── wiki/
        ├── index.md
        ├── log.md
        └── topics/
            ├── <topic>.md
            └── <topic>/
                └── <subtopic>.md
```

각 요소의 책임은 다음과 같습니다.

| 위치 | 책임 |
| --- | --- |
| `CLAUDE.md` | 모든 ingest에서 유지되는 소유권, Topic, 근거, drift 작성 규칙 |
| `.llm-wiki/prompts/ingest.md` | 한 번의 `main` 변경을 처리하는 순서와 런타임 입력 위치 |
| `.github/workflows/wiki-ingest.yml` | trigger, job 권한, concurrency, artifact 전달을 담당하는 orchestration |
| `scripts/wiki/` | context 생성, 검증, patch 직렬화, PR 게시를 담당하는 결정적 로직 |
| `docs/specs/` | 사람이 작성하고 승인하는 Spec, LLD, acceptance criteria |
| `docs/wiki/index.md` | 전체 Topic의 링크와 한 줄 요약을 담는 탐색 진입점 |
| `docs/wiki/log.md` | Wiki 문서 변경의 source commit, Topic, drift를 기록하는 append-only 이력 |
| `docs/wiki/topics/` | LLM이 생성하고 갱신하는 현재 지식 |

## Topic은 작게 시작하고 필요할 때 분리한다

처음 발견한 지식은 하나의 Topic 문서로 시작합니다.

```text
docs/wiki/topics/search-service.md
```

내용이 커지면 대표 문서는 기존 경로에 유지하고, 독립적으로 탐색할 가치가 있는 세부 내용만 하위 문서로 분리합니다.

```text
docs/wiki/topics/
├── search-service.md
└── search-service/
    ├── request-mapping.md
    ├── response-mapping.md
    ├── timeout-and-retry.md
    └── fallback.md
```

`search-service.md`는 요약과 하위 문서 링크를 제공하는 허브가 됩니다. 대표 문서의 경로를 유지하므로 기존 링크가 깨지지 않습니다.

다음 중 하나에 해당하면 하위 Topic으로 분리합니다.

- 다른 문서에서 독립적으로 링크할 가치가 있습니다.
- 대표 Topic과 변경 주기가 다릅니다.
- 여러 지면이나 흐름에서 반복해서 사용됩니다.
- 하나의 독립적인 질문에 답할 수 있습니다.
- 문서가 길어져 관련 부분만 읽기 어려워집니다.

## 코드와 문서가 다를 때

LLM은 불일치를 임의로 해결하지 않습니다.

- 병합된 코드가 기존 Wiki와 다르면 현재 구현을 근거로 Wiki 갱신을 제안합니다.
- 승인된 Spec과 코드가 다르면 어떤 쪽이 맞는지 단정하지 않고 drift로 보고합니다.
- LLM은 사람 소유의 Spec·LLD를 현재 코드에 맞춰 자동으로 덮어쓰지 않습니다.

`docs/specs/`가 없는 저장소에서도 동작합니다. 이 경우 코드와 테스트를 근거로 Wiki를 갱신하고 Spec drift 검사는 건너뜁니다.

## 설치

v1은 원격 reusable Action이 아니라 대상 저장소 안에 함께 두는 toolkit입니다. 다음 경로를 대상 저장소에 복사합니다.

```text
CLAUDE.md
.github/workflows/wiki-ingest.yml
.github/workflows/ci.yml
.llm-wiki/prompts/ingest.md
scripts/wiki/
```

대상 저장소에 `CLAUDE.md`가 이미 있으면 파일 전체를 덮어쓰지 말고 이 프로젝트의 LLM Wiki compiler policy를 기존 지침에 병합합니다.

GitHub 저장소에는 다음 설정이 필요합니다.

1. Actions secret `ANTHROPIC_API_KEY`를 등록합니다.
2. Actions variable `LLM_WIKI_ENABLED`를 `true`로 등록합니다. 이 변수가 없거나 다른 값이면 ingest job은 실행되지 않습니다.
3. Workflow가 `contents: write`와 `pull-requests: write`를 사용할 수 있게 합니다.
4. **Settings → Actions → General**에서 GitHub Actions의 Pull Request 생성을 허용합니다.
5. `main` direct push를 막고 PR merge만 허용하는 branch protection을 권장합니다.

설치 이전의 Git 이력을 자동으로 역주행하지 않습니다. 활성화 이후 첫 번째 `main` push의 `before..after` 범위부터 쌓기 시작합니다. branch 생성처럼 `before`가 zero SHA인 event는 과거 전체를 읽지 않고 bootstrap 안내와 함께 종료합니다.

## 실행 안전성

- GitHub event 값은 prompt나 inline shell에 직접 삽입하지 않고 환경 변수와 JSON context로 전달합니다.
- Claude CLI는 `--tools`로 읽기·검색·Wiki 편집 도구만 노출하고 MCP, slash command, session persistence를 끕니다.
- Claude가 수정한 작업 트리는 Claude가 수정할 수 없는 pristine checkout의 엔진으로 허용 경로, symlink, append-only log, source key, Topic 링크, 근거 commit, 파일 수, patch 크기를 검사합니다.
- publish job은 저장소 밖으로 복사한 trusted engine을 사용합니다. artifact checksum을 확인하고 disposable worktree에서 patch를 적용·재검증한 뒤에만 실제 managed branch를 갱신합니다.
- 외부 GitHub Actions는 full commit SHA로 고정하고 Claude Code CLI 버전도 고정합니다.
- 기본 한도는 source 변경 200개·입력 diff 1 MB·Wiki 변경 30개·Wiki patch 500 KB·Claude 8 turns입니다.
- 변경할 지식이 없으면 처리 cursor만 managed branch에 기록하고 빈 PR은 만들지 않습니다.
- Wiki만 변경된 push는 Claude를 호출하지 않아 생성된 Wiki PR의 merge가 다시 ingest되는 순환을 막습니다.

기본 `GITHUB_TOKEN`으로 생성한 PR은 다른 workflow를 자동으로 trigger하지 않을 수 있습니다. 이 프로젝트는 그 동작에 안전성을 의존하지 않고 publish 직전 이중 검증을 수행합니다. 생성된 Wiki PR에서도 일반 CI를 자동 실행해야 한다면 후속 구성에서 GitHub App installation token을 사용해야 합니다.

## 로컬 검증

실제 Anthropic API나 GitHub 저장소를 호출하지 않고 임시 Git 저장소와 fake Claude/GitHub CLI로 pipeline을 검증할 수 있습니다.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
shellcheck scripts/wiki/*.sh
actionlint
git diff --check
```

## 프로젝트가 지향하지 않는 것

- 기존 3년치 코드베이스를 한 번에 완전하게 문서화하는 것
- 코드 검증 없이 LLM의 추론을 사실로 저장하는 것
- Spec과 LLD를 LLM이 임의로 현재 코드에 맞추는 것
- 처음부터 복잡한 검색 인프라나 벡터 데이터베이스를 도입하는 것
- 예제 서비스 코드베이스나 완성된 Wiki 내용을 이 저장소에 포함하는 것

Wiki는 실제로 사용되는 지식부터 성장합니다. 구조와 규칙도 지식이 쌓이면서 필요한 만큼 함께 진화합니다.
