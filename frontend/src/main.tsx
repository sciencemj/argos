import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import "./index.css";
import { Shell } from "./layout/Shell";
import { CalendarTab } from "./pages/CalendarTab";
import { ChannelPage, FeedTab } from "./pages/ChannelPage";
import { KanbanTab } from "./pages/KanbanTab";
import { TodayPage } from "./pages/TodayPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <TodayPage /> },
      {
        path: "c/:channelId",
        element: <ChannelPage />,
        children: [
          { index: true, element: <FeedTab /> },
          { path: "kanban", element: <KanbanTab /> },
          { path: "calendar", element: <CalendarTab /> },
        ],
      },
    ],
  },
]);

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
