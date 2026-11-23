// Argos site: theme and language toggles (same storage keys as the app) and the intro
// video player. No build step, no dependencies.
(() => {
  const root = document.documentElement;
  const THEME_KEY = "argos.theme";
  const LANG_KEY = "argos-language";

  const store = {
    get(key) {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, value);
      } catch {
        // Private mode or blocked storage: the choice lasts for this page only.
      }
    },
  };

  // Theme ---------------------------------------------------------------------
  const darkQuery = matchMedia("(prefers-color-scheme: dark)");
  const themeButton = document.getElementById("theme-toggle");

  function applyTheme(theme) {
    root.dataset.theme = theme;
    const ko = root.lang === "ko";
    const next = theme === "dark" ? (ko ? "라이트 모드로" : "Switch to light") : ko ? "다크 모드로" : "Switch to dark";
    themeButton.setAttribute("aria-label", next);
    themeButton.title = next;
  }

  themeButton.addEventListener("click", () => {
    const theme = root.dataset.theme === "dark" ? "light" : "dark";
    store.set(THEME_KEY, theme);
    applyTheme(theme);
  });

  // Follow the OS until the visitor picks a theme.
  darkQuery.addEventListener("change", (event) => {
    const saved = store.get(THEME_KEY);
    if (saved !== "light" && saved !== "dark") applyTheme(event.matches ? "dark" : "light");
  });

  // Video ---------------------------------------------------------------------
  // On GitHub Pages the workflow copies docs/promo into media/. Opened from the repo
  // (site/index.html served from the repository root) the files are at ../docs/promo/.
  const LOCAL_BASE = "../docs/promo/";
  const PAGES_BASE = "media/";
  let mediaBase = /\/site\/(index\.html)?$/.test(location.pathname) ? LOCAL_BASE : PAGES_BASE;

  const video = document.getElementById("intro-video");
  const promoLink = document.getElementById("promo-html");
  let videoLang = null;
  let triedFallback = false;

  function setVideoLang(lang) {
    if (lang === videoLang) return;
    const resumeAt = videoLang && video.currentTime > 0 ? video.currentTime : 0;
    const wasPlaying = videoLang !== null && !video.paused;
    videoLang = lang;
    video.poster = `assets/poster-${lang}.jpg`;
    video.src = `${mediaBase}argos-intro.${lang}.mp4`;
    video.setAttribute("aria-label", lang === "ko" ? "Argos 소개 영상 (한국어)" : "Argos intro video (English)");
    promoLink.href = `${mediaBase}argos-intro.${lang}.html`;
    if (resumeAt > 0) {
      video.addEventListener(
        "loadedmetadata",
        () => {
          video.currentTime = resumeAt;
          if (wasPlaying) video.play().catch(() => {});
        },
        { once: true },
      );
    }
    for (const button of document.querySelectorAll("[data-video-lang]")) {
      button.setAttribute("aria-pressed", String(button.dataset.videoLang === lang));
    }
  }

  // If the guessed location is wrong (a custom deploy), try the other one once.
  video.addEventListener("error", () => {
    if (triedFallback) return;
    triedFallback = true;
    mediaBase = mediaBase === PAGES_BASE ? LOCAL_BASE : PAGES_BASE;
    const lang = videoLang;
    videoLang = null;
    setVideoLang(lang);
  });

  for (const button of document.querySelectorAll("[data-video-lang]")) {
    button.addEventListener("click", () => setVideoLang(button.dataset.videoLang));
  }

  // Language ------------------------------------------------------------------
  const META = {
    ko: {
      title: "Argos · 에이전트를 곁들인 나만의 워크스페이스",
      description:
        "Argos는 과목과 프로젝트마다 채팅·칸반·캘린더를 두고, AI 에이전트와 함께 일하는 내 Mac 전용 워크스페이스예요.",
    },
    en: {
      title: "Argos · Your workspace with agents",
      description:
        "Argos gives every course and project its own chat, kanban and calendar, and lets AI agents work beside you, all on your Mac.",
    },
  };

  function applyLang(lang) {
    root.lang = lang;
    document.title = META[lang].title;
    document.querySelector('meta[name="description"]').setAttribute("content", META[lang].description);
    for (const button of document.querySelectorAll("[data-set-lang]")) {
      button.setAttribute("aria-pressed", String(button.dataset.setLang === lang));
    }
    applyTheme(root.dataset.theme === "dark" ? "dark" : "light");
    // The video follows the page language unless it is playing.
    if (video.paused) setVideoLang(lang);
  }

  for (const button of document.querySelectorAll("[data-set-lang]")) {
    button.addEventListener("click", () => {
      store.set(LANG_KEY, button.dataset.setLang);
      applyLang(button.dataset.setLang);
    });
  }

  applyLang(root.lang === "en" ? "en" : "ko");
})();
