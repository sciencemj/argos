# Argos 소개 영상

72초(1920×1080, 30fps) 기능 소개 영상. HTML + GSAP 애니메이션이 원본이고 mp4는 거기서 뽑은 것이다.

| 파일 | 내용 |
| --- | --- |
| `argos-intro.ko.html` / `argos-intro.en.html` | 브라우저에서 바로 재생(재생·일시정지·타임라인 막대). `#p30`을 붙이면 30초에서 멈춘 채로 열린다 |
| `argos-intro.ko.mp4` / `argos-intro.en.mp4` | 렌더링 결과 |
| `capture.mjs` | HTML → mp4 프레임 단위 렌더러 |

장면: 인트로 → 오늘(KPI·앞으로 7일·달력) → 채팅으로 적기 → 에이전트(다크, PDF 첨부) → 칸반과 잡 → 학교 LMS(LearningX 가져오기·공지 요약) →
연결(캘린더·옵시디언·빠른 입력·승인·폰/Tailscale) → 아웃트로. 화면 속 데이터는 `docs/design` 캔버스의 데모 데이터를 따르고,
캔버스가 없는 화면(새 오늘 화면 등)은 실제 앱 화면을 옮겼다.

장면 시간은 각 장면 코드의 절대 시각이고, `scene(offset)`이 그 뒤 장면 전체를 민다. 장면을 늘리면 다음 장면들의
`scene()` 값과 끝 시각(`tl.to({}, …, 60)` + offset, 배경 줄무늬 길이)을 같이 고친다.

## 다시 렌더링

HTML을 고친 뒤(ffmpeg, Google Chrome 필요, 다른 Chrome은 `CHROME_PATH`로):

```bash
cd docs/promo
bun --install=force capture.mjs argos-intro.ko.html argos-intro.ko.mp4   # playwright-core는 bun이 자동 설치
bun --install=force capture.mjs argos-intro.en.html argos-intro.en.mp4
```
