# LMS 액세스 토큰 연결 (기본 방법)

학교 LMS가 Canvas라면 개인 액세스 토큰 하나로 Argos가 과제·공지·최근 활동·메시지 요약과 주차학습 자료를
가져옵니다. 브라우저나 LMS 탭을 열어 둘 필요가 없고, Argos가 켜져 있는 동안 15분마다(`ARGOS_LMS_POLL_MINUTES`,
0이면 끔) 그리고 설정의 **지금 가져오기**로 동기화합니다. 고려대(`lms.korea.ac.kr`)를 포함해 Canvas를 쓰는
학교라면 주소만 바꿔 쓸 수 있습니다.

## 연결

1. LMS에 로그인한 뒤 왼쪽 메뉴의 **계정 → 설정**으로 갑니다.
2. 아래쪽 **승인된 통합(Approved Integrations)**에서 **새 액세스 토큰(New Access Token)**을 누릅니다.
3. 용도(예: Argos)와 만료일을 정하고 토큰을 생성합니다. 학교마다 최대 기간이 있습니다(고려대는 120일로 안내됨).
4. Argos 설정 → **학교 LMS**에 LMS 주소(예: `https://lms.korea.ac.kr`)와 토큰을 붙여 넣고 **연결**을 누릅니다.

연결할 때 Argos는 토큰으로 내 프로필을 한 번 조회해 확인합니다. 맞지 않으면 저장하지 않습니다. 확인되면 계정
이름이 표시되고 바로 첫 동기화를 시작합니다. **주차학습 자료도 저장**을 켜면 과목 모듈의 파일(25MB까지)을 받아
과목 피드에 첨부합니다(기본 꺼짐).

토큰이 만료되거나 LMS에서 지우면 설정에 "토큰이 만료됐거나 취소됐어요"가 표시됩니다. 새 토큰을 발급해
**토큰 바꾸기**로 다시 연결하세요. 연결을 해제해도 가져온 과목·할 일·알림은 남습니다.

## 가져오는 것

확장 프로그램과 같은 Canvas API를 같은 방식으로 읽으므로 결과(과목 채널, 과제 → 할 일, 공지·활동·메시지 →
알림 카드, 자료 첨부)와 중복 방지 규칙이 같습니다. 두 방법을 함께 써도 같은 항목이 두 번 생기지 않습니다.

- 과목: `GET /api/v1/courses?enrollment_type=student&enrollment_state=active`
- 과제·공지: `/api/v1/courses/:id/assignments?include[]=submission`, `/discussion_topics?only_announcements=true`
- 최근 활동·메시지: `/api/v1/users/self/activity_stream`, `/api/v1/conversations`
- 자료(켠 경우): `/modules?include[]=items`, `/files/:id`, `/files/:id/public_url`

**출결 표는 가져오지 않습니다.** 출결은 API가 아니라 화면의 표를 읽는 방식이라 확장 프로그램에서만 됩니다.

## 보안

- 토큰은 macOS 키체인(서비스 `Argos LMS access token`, 계정은 LMS 주소)에만 저장합니다. DB·설정 파일·로그·
  WebSocket 이벤트·API 응답에 넣지 않습니다. `app_setting.lms_canvas`에는 주소·계정 이름·계정 id만 둡니다.
- Canvas 개인 토큰은 보통 범위 제한이 없어 계정 권한 전체를 가집니다. Argos는 GET만 보내고, 설정한 LMS 주소의
  `/api/v1/` 아래로만 토큰을 보냅니다. 다음 페이지 링크가 다른 주소를 가리키면 따라가지 않고 경고로 남깁니다.
- 리다이렉트는 자동으로 따르지 않습니다. 파일을 받을 때 다른 주소(학교 파일 저장소)로 넘어가면 토큰 없이
  요청하고, 서명된 저장소 주소는 저장하지 않으며 데스크톱 HTTP 로그에서도 쿼리를 지웁니다.
- 연결·해제·지금 가져오기는 이 Mac의 Argos 화면에서만 됩니다(다른 사이트·Tailscale로는 거절).

## 확장 프로그램(대안)

토큰을 발급할 수 없는 학교이거나 출결 표가 필요하면 설정의 **다른 연결 방법: 브라우저 확장 프로그램**을
펼쳐 [확장 프로그램 연결](lms-extension.md)을 쓰세요.

## 검증

`backend/tests/test_lms_canvas.py`가 가짜 Canvas(httpx `MockTransport`, 고려대가 아닌 주소)로 틀린 토큰 거절,
키체인 저장·해제, 과목 목록 다음 페이지, 반복 동기화 멱등, 원격 수정 반영(로컬 상태 유지), 자료 다운로드 시
저장소에 토큰 미전송·서명 비노출·재다운로드 없음, 토큰 취소 안내, 다른 주소 링크 거부를 확인합니다.
`frontend/src/pages/LmsSettings.test.tsx`가 화면을 확인합니다. 실제 학교 LMS 토큰으로는 아직 확인하지
않았습니다(특히 `public_url`이 주는 저장소 주소에서 토큰 없이 파일을 받는지).
