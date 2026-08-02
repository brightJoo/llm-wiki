# LLM Wiki

운영 중인 코드베이스의 지식을 GitHub의 변경 이력에서 점진적으로 축적하는 LLM 기반 Wiki입니다.

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

## LLM Wiki 구조

```text
.
├── CLAUDE.md
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
| `CLAUDE.md` | LLM이 따라야 하는 ingest, query, lint, 문서 작성 규칙 |
| `docs/specs/` | 사람이 작성하고 승인하는 Spec, LLD, acceptance criteria |
| `docs/wiki/index.md` | 전체 Topic의 링크와 한 줄 요약을 담는 탐색 진입점 |
| `docs/wiki/log.md` | ingest, query, lint와 문서 변경을 기록하는 append-only 이력 |
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

## 프로젝트가 지향하지 않는 것

- 기존 3년치 코드베이스를 한 번에 완전하게 문서화하는 것
- 코드 검증 없이 LLM의 추론을 사실로 저장하는 것
- Spec과 LLD를 LLM이 임의로 현재 코드에 맞추는 것
- 처음부터 복잡한 검색 인프라나 벡터 데이터베이스를 도입하는 것

Wiki는 실제로 사용되는 지식부터 성장합니다. 구조와 규칙도 지식이 쌓이면서 필요한 만큼 함께 진화합니다.
