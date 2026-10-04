# brightJoo 코드 기반 LLM Wiki 구축 설계 초안

작성일: 2026-10-04 (Asia/Seoul)

상태: 2026-10-04 문서 분류·갱신 설계 v2의 분류 계약·검증·게시 전달 구현 및 독립 검토 완료. PR #3으로 검토 가능. 실제 생성 실행기 연결·정기 실행 활성화는 후속 단위.

## 목적

brightJoo의 main에 쌓이는 코드 변경을 LLM으로 문서화하고, 이후 작업에서 이전 구현과 변경 이유를 빠르게 파악할 수 있는 Wiki 운영. main 코드를 SSOT로 유지하고 Docs를 해당 코드에서 파생된 설명으로 관리.

**사용자 제약:** API 키를 등록하지 않는 오픈소스 도구로 구축. 설치, 테스트, 기본 CI에서 유료 LLM API 호출을 요구하지 않음. 문서화 엔진과 LLM 실행기를 분리하고 API 키 없는 실행기를 연결할 수 있도록 구성.

## 확인한 기존 구현

대상 엔진: https://github.com/brightJoo/llm-wiki

확인한 main 커밋: `b091099de6529f1ce5436d54dd86123280f17988`

- Python으로 변경 범위·입력 context 생성, 생성 결과 검증
- Claude Code CLI로 Wiki 컴파일
- 생성 단계와 PR 게시 단계를 분리한 GitHub Actions
- `docs/wiki/index.md`, `docs/wiki/log.md`, `docs/wiki/topics/**` 관리
- `wiki/pending` 브랜치와 문서 PR에 미병합 문서 누적
- commit trailer와 Wiki log에서 처리한 source cursor 복원
- 현재 트리거는 `push`와 `workflow_dispatch`. 일별 `schedule`은 없음
- 현재 저장소 Settings에 Repository secret과 Repository variable이 없음
- 기존 변경 범위 테스트 16개 로컬 통과. 실제 LLM·원격 게시 실행은 미검증

GitHub 커넥터는 업무 계정에 연결돼 있으나 브라우저에서는 brightJoo 저장소 소유자 접근을 확인. 개인 계정 연결 방식은 실제 게시 전에 확정.

## 적용 방식 선택

| 방식 | 특징 | 추천 |
| --- | --- | --- |
| 기존 엔진을 보완해 한 코드 저장소에 설치 | 저장소 안에서 코드·테스트와 Wiki를 함께 검증. 현재 구현을 활용할 수 있음 | 첫 적용에 추천 |
| llm-wiki에서 여러 저장소를 읽어 통합 Wiki 운영 | 저장소별 커서·출처 분리와 저장소 간 접근 설정 필요 | 여러 저장소 통합이 실제 목적일 때 선택 |
| 새로운 자동화 엔진 생성 | 기존 구현과 관리 대상이 중복됨 | 기존 엔진으로 충족할 수 없는 요구가 생길 때 검토 |

아래 설계는 첫 번째 방식을 기준으로 작성. 문서화할 코드 저장소는 사용자 선택 후 설치 대상으로 확정.

## 실행 흐름

1. 매일 정해진 시각 또는 수동으로 Workflow 실행
2. 실행 시작 시 대상 main의 head SHA 고정
3. 마지막으로 처리한 source SHA부터 고정 head까지 변경 수집
4. diff, 현재 코드·테스트, 관련 Wiki 문서를 함께 확인
5. 저장소에 둔 문서화 규칙과 프롬프트로 기능 문서·목차·로그 갱신
6. 기존 검증기로 허용 경로, 링크, 출처, 로그 누적 형식 확인
7. 검증한 결과를 관리 브랜치와 문서 PR에 반영
8. 저장에 성공한 경우 처리 cursor 갱신

실패한 날의 변경도 다음 실행에서 이어서 처리. 하루 동안 아무 변경이 없으면 LLM 호출 생략. Wiki만 바뀐 변경은 소스 문서화 대상에서 제외.

## LLM 실행기 분리

현재 `run_compiler.sh`는 `CLAUDE_BIN`을 바꿀 수 있지만 Claude 전용 인자를 전달하고 Claude 응답 형식을 검사. 실행 파일 경로 변경만으로 다른 LLM을 지원한다고 볼 수 없음.

코어 엔진은 Git 변경 수집, Wiki context 구성, 생성 결과 검증, patch 생성, PR 게시를 담당. LLM 실행기는 context와 문서화 규칙을 입력받아 작업 트리의 Wiki 문서를 생성·갱신하는 별도 adapter로 연결.

- 기본 CI: 테스트용 compiler를 사용해 전체 엔진 흐름 검증. API 키·실제 LLM 호출 없이 실행
- 실사용: 사용자가 지정한 로컬 LLM 또는 CLI adapter로 Wiki 생성
- 실행기 미설정: context 준비와 검증 기능 사용 가능. 생성 단계를 자동 성공 처리하거나 처리 cursor를 진행시키지 않음
- 실제 문서 생성 성공 여부는 선택한 실행기를 연결해 별도로 검증

기존 Claude 호출부는 API 키 없는 실행을 기본으로 바꾸는 과정에서 분리 대상. 특정 상용 API 연결을 사용자의 설치 전제로 두지 않음.

## 첫 실행과 처리 기준

처음에는 설치를 시작한 시점의 main SHA를 명시적으로 기준으로 등록. 이후 변경부터 축적. 과거 작업을 포함하고 싶으면 수동 실행에서 검토한 base SHA를 지정.

저장된 cursor가 현재 main의 조상이 아니거나 사라진 경우 실행을 멈추고 기준 재설정을 요청. 임의로 다른 기준을 택해 누락을 숨기지 않음.

입력 크기가 기존 한도를 초과하면 실패 원인과 범위를 보고하고 cursor 유지. 초기 버전에서 자동 분할 정책은 추가하지 않음.

## Docs 구성

문서 분류의 기준은 독립적으로 답할 수 있는 질문, 그 답의 적용 범위, 함께 변경되는 지식의 경계. [전시 도메인 문서 분류 기준](2026-10-04-llm-wiki-topic-structure.md)에 공통 정책·화면별 예외·Topic 생성·갱신 규칙과 검증 사례를 정리.

분류 설계 v2는 사람 소유 도메인 프로필, Topic의 JSON 메타데이터, 유형별 관계, 분류 이유·미확인 범위, 최종 head 기준의 일별 동작, 기존 문서의 점진 전환을 구체화. [홈·랭킹 예제](2026-10-04-llm-wiki-display-example.md)에 네 문서와 하루의 변경 배치 결과 포함. 모두 구현·평가 전 설계이며 실제 회사 정책이나 과거 구축 성과가 아님.

기존 엔진의 문서 경로를 유지:

```text
docs/wiki/
  index.md
  log.md
  topics/
    <feature>.md
    <feature>/<subtopic>.md
```

- Topic: 현재 동작, 적용 범위, 예외·제약, 코드와 승인된 설계의 차이, 코드 경로·커밋 SHA, 관련 Topic 링크
- Index: Topic 링크와 한 줄 설명
- Log: source SHA, 바뀐 Topic, 설계와 구현의 차이. 기존 내용은 유지하고 새 기록 추가

기능 설명은 관련 코드가 바뀔 때 기존 문서에 반영. 메서드 주석을 자동으로 바꾸는 흐름은 Wiki 생성 권한과 분리된 후속 작업으로 다룸.

## 필요한 실행 설정

- API 키 없이 설치·테스트·기본 CI 실행
- 실제 문서화를 사용할 환경의 compiler adapter 설정
- 실제 문서화 활성화 시 `LLM_WIKI_ENABLED=true`
- 첫 기준 커밋 설정
- 매일 실행할 시각 설정: 기존 대화의 새벽 3시는 예시이므로 운영 시각은 확정 전
- PR 게시 단계의 쓰기 권한과 Actions의 PR 생성 허용 설정 확인

API 키 등록은 이 프로젝트 구축의 필수 단계에서 제외. 기본 CI와 예제 검증에서 유료 API를 호출하지 않음.

## 변경할 부분

구현 단위를 분리: 먼저 문서 분류·갱신 계약과 검증 보완, 이후 실행기 adapter와 정기 실행. 이번 설계 보강의 초점은 첫 단위이며 API 키 없는 실행 제약은 두 단위 모두 유지.

- `.github/workflows/wiki-ingest.yml`: 정기 실행과 첫 기준 커밋 입력 연결, 특정 LLM API key에 대한 필수 의존 제거
- `scripts/wiki/run_compiler.sh`: Claude 전용 호출을 compiler adapter 경계로 분리
- `scripts/wiki/prepare_context.py`: 정기 실행에서 cursor·기준 커밋을 선택하는 경로 보완
- 변경 범위 테스트: 여러 main 변경의 일괄 처리, 무변경, 실패 후 재실행, 첫 실행, 잘못된 기준 처리
- `README.md`: API 키 없는 설치·검증 절차, 실행기 계약, 정기 실행·초기화·복구 절차
- 필요할 때 `.llm-wiki/prompts/ingest.md`: 한 실행의 입력이 여러 병합 변경을 포함함을 명시
- 문서화 규칙: Topic의 질문·유형·적용 범위·참조 관계, 분류 이유와 근거를 명시해 공통 정책의 중복과 화면별 예외의 오분류 검토
- 분류 지원: 도메인 프로필을 지정했을 때 메타데이터·관계 검사, 기존 Topic의 점진 전환, 분류 설명의 별도 compiler 결과 계약
- 기존 검증 보완: Sources 예시와 실제 파서 형식 일치, 일별 변경 범위 중간에서 삭제된 경로의 출처 확인

## 완료 확인 기준

1. 같은 날 여러 코드 변경이 한 실행의 입력에 포함
2. 현재 Topic에 코드 근거와 기준 SHA 반영
3. 같은 source SHA를 재실행해도 로그가 중복되지 않음
4. LLM·검증·게시 실패 시 다음 실행에서 해당 변경을 다시 처리
5. 미병합 문서가 있는 상태에서도 다음 변경이 동일 PR에 누적
6. Wiki만 변경되거나 main 변경이 없으면 불필요한 LLM 호출 생략
7. API 키 없이 테스트용 compiler로 문서 생성·검증·PR 게시 계약 확인
8. 선택한 실제 실행기 연결 후 수동 실행으로 실제 문서 PR 생성 확인. 테스트용 compiler 결과와 구분해 보고

## 확정할 항목

- 문서화할 코드 저장소: 한 저장소부터 적용할지, 여러 저장소를 통합할지
- 실제 문서 생성에 연결할 API 키 없는 LLM 실행기
- 운영 시각과 첫 source 기준

## 참고

- [기존 llm-wiki 구현](https://github.com/brightJoo/llm-wiki)
- [GitHub Actions schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): 기본 브랜치에서 실행하며 지연·누락 가능성이 있어 고정된 24시간 창보다 cursor 기반 처리를 사용
- [LLM Wiki 원문](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): 자료를 기존 지식에 반영하고 목차·갱신 로그를 유지하는 패턴
