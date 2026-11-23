# Argos 소개 영상

83초(1920×1080, 30fps) v0.2 기능 소개 영상. HTML + GSAP 애니메이션이 원본이고 mp4는 거기서 뽑은 것이다.

| 파일 | 내용 |
| --- | --- |
| `argos-intro.ko.html` / `argos-intro.en.html` | 브라우저에서 바로 재생(재생·일시정지·타임라인 막대). `#p30`을 붙이면 30초에서 멈춘 채로 열린다 |
| `argos-intro.ko.mp4` / `argos-intro.en.mp4` | 렌더링 결과 |
| `capture.mjs` | HTML → mp4 프레임 단위 렌더러 |

장면: 인트로 → 오늘(KPI·앞으로 7일·달력, 할 일을 끝내면 뛰는 Argos 강아지) → 채팅(에이전트 없이 모델 선택기로 모델·추론 수준을 고르고,
기본 채팅이 `도구 2개 실행`으로 할 일을 기록) → 에이전트(다크, PDF 첨부 + @멘션, `render_artifact` 그림) → 칸반과 잡 →
LMS·지식 검색(LearningX 가져오기·공지 요약, `search_knowledge`) → 아침 요약(#today 스레드·메신저) →
연결(캘린더 덧씌움·옵시디언·빠른 입력·승인·폰/Tailscale·에이전트 도구 MCP/스킬 따로) → 아웃트로. 화면 속 데이터는 `docs/design` 캔버스의 데모 데이터를 따르고,
캔버스가 없는 화면(새 오늘 화면 등)은 실제 앱 화면을 옮겼다.

장면 시간은 각 장면 코드의 절대 시각이고, `scene(offset)`이 그 뒤 장면 전체를 민다(지금 채팅·에이전트 +1, 칸반 +2.5, LMS 0,
아침 요약 +1.5, 연결·아웃트로 +22.5). 장면을 늘리면 다음 장면들의 `scene()` 값과 끝 시각(`tl.to({}, …, 60)` + offset,
배경 줄무늬 길이, 컨트롤의 `00:00 / 01:23`)을 같이 고친다.

렌더링은 프레임마다 시각을 옮기고 찍는다. 3D로 기운 창 안에서 숨겨 둔 요소를 바꿔 보이게 하면 Chrome이 그 요소를
다시 그리지 않을 수 있어서(칸반 카드가 사라진 버그), 탐색(`#p43`)으로는 멀쩡해도 mp4 프레임을 꼭 확인한다.

## 다시 렌더링

HTML을 고친 뒤(ffmpeg, Chrome 필요. 기본은 설치된 Google Chrome이고, Playwright가 받은 Chromium/Chrome for Testing은
`CHROME_PATH`로 지정, 예: `~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`):

```bash
cd docs/promo
bun --install=force capture.mjs argos-intro.ko.html argos-intro.ko.mp4   # playwright-core는 bun이 자동 설치
bun --install=force capture.mjs argos-intro.en.html argos-intro.en.mp4
```
