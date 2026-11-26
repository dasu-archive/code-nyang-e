# code-nyang-e

미니 PC에서 돌아가는 코드 검사기.
Jenkins + SonarQube Community Build + Claude Code 리뷰 + 리포트 스크립트.
결과는 dasu-archive/dashboard 레포(GitHub Pages)에 JSON으로 올린다.

## 정본과 소통 (Notion MCP)
- 정본이자 소통 창구는 Notion이다. 주소는 이 레포에 두지 않는다.
- 주소는 CLAUDE.local.md(커밋 안 함)에 있다. 그 파일이 없으면 Notion MCP로 "Code NyangE" 페이지를 검색해서 찾는다.
- 내 상세 페이지는 공통 페이지 안의 "내부" 페이지, 이슈 DB와 요구사항 DB도 공통 페이지 안에 있다.
- 나는 "내부"다. 외부(dashboard)를 만드는 Claude Code와는 직접 연결되어 있지 않다.

## 세션 루틴
1. 공통 페이지와 내 상세 페이지를 읽는다.
2. 요구사항 DB에서 받는 쪽 = 내부, 상태 = 요청/수락/진행 중인 것을 확인하고 처리한다.
3. 작업한다.
4. 내 문제·할 일은 이슈 DB(영역 = 내부, 버전 = 지금 작업 중인 내부 버전. 현재 1.0.0). 외부에 부탁할 것은 요구사항 DB(보낸 쪽 = 내부, 받는 쪽 = 외부).
   도건이 정할 것은 담당 또는 받는 쪽 = 도건.
5. 끝나면 내 상세 페이지의 "현재 상태"와 작업 목록 체크를 갱신한다.

## 규칙
- Notion 주소·ID는 레포 파일, 커밋 메시지, PR, 코드 주석에 쓰지 않는다.
- CLAUDE.local.md는 올리지 않는다 (Notion 주소가 들어 있음). CLAUDE.md는 주소 없이 커밋한다.
- 데이터 계약 정본은 schema/v1/. 바꾸려면 요구사항 DB에 요청하고 수락된 뒤에 바꾼다.
- dashboard 레포에는 projects.json, projects/<id>/latest.json, projects/<id>/history.json,
  projects/<id>/index.html(project-template.html 복사본)만 쓴다.
- 공개 JSON의 숫자는 API·스크립트 값 그대로. AI는 findings와 해석(opinion)만 만든다.
- 공개 전 allowlist와 schema 검증을 반드시 통과시킨다.
- 토큰은 Jenkins Credentials에만 둔다. .env 커밋 금지, git add . 금지.
- claude -p에 --bare를 쓰지 않는다 (Max 구독 로그인 사용).
- 검사 대상 레포의 코드는 고치지 않는다.