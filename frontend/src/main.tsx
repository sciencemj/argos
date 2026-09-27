import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { inDesktopApp } from "./desktop";
import { syncCurrentLanguage } from "./i18n";
import "./index.css";
import { Shell } from "./layout/Shell";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { CalendarTab } from "./pages/CalendarTab";
import { ChannelPage, FeedTab } from "./pages/ChannelPage";
import { KanbanTab } from "./pages/KanbanTab";
import { MaterialsTab } from "./pages/MaterialsTab";
import { NotesTab } from "./pages/NotesTab";
import { NotificationsPage } from "./pages/NotificationsPage";
import { QuickCapture } from "./pages/QuickCapture";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TodayPage } from "./pages/TodayPage";
import { WelcomePage } from "./pages/Welcome";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  { path: "/quick", element: <QuickCapture /> }, // the desktop app's capture window
  { path: "/welcome", element: <WelcomePage /> }, // first-run setup
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <TodayPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "approvals", element: <ApprovalsPage /> },
      { path: "notifications", element: <NotificationsPage /> },
      { path: "review", element: <ReviewPage /> },
      {
        path: "c/:channelId",
        element: <ChannelPage />,
        children: [
          { index: true, element: <FeedTab /> },
          { path: "kanban", element: <KanbanTab /> },
          { path: "calendar", element: <CalendarTab /> },
          { path: "notes", element: <NotesTab /> },
          { path: "materials", element: <MaterialsTab /> },
        ],
      },
    ],
  },
]);

// In the desktop app the window has no title bar: the traffic lights sit over the rail.
if (inDesktopApp()) document.documentElement.dataset.desktop = "";
syncCurrentLanguage();

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
