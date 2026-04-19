import { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter, redirect } from "react-router";
import { queryClient } from "./api/queryClient";
import { setOn401 } from "./api/client";
import { Toaster } from "@/components/ui/toaster";

const router = createBrowserRouter([
  {
    path: "/",
    lazy: async () => ({ Component: (await import("./routes/_authed")).default }),
    children: [
      { index: true, loader: () => redirect("/detectors") },
      {
        path: "detectors",
        children: [
          { index: true, lazy: async () => ({
            Component: (await import("./routes/_authed.detectors._index")).default,
            handle: (await import("./routes/_authed.detectors._index")).handle,
          })},
          { path: "new", lazy: async () => ({
            Component: (await import("./routes/_authed.detectors.new")).default,
            handle: (await import("./routes/_authed.detectors.new")).handle,
          })},
          { path: ":id", lazy: async () => ({
            Component: (await import("./routes/_authed.detectors.$id")).default,
            handle: (await import("./routes/_authed.detectors.$id")).handle,
          })},
        ],
      },
      {
        path: "datasets",
        children: [
          { index: true, lazy: async () => ({
            Component: (await import("./routes/_authed.datasets._index")).default,
            handle: (await import("./routes/_authed.datasets._index")).handle,
          })},
          { path: "new", lazy: async () => ({
            Component: (await import("./routes/_authed.datasets.new")).default,
            handle: (await import("./routes/_authed.datasets.new")).handle,
          })},
          { path: ":id", lazy: async () => ({
            Component: (await import("./routes/_authed.datasets.$id")).default,
            handle: (await import("./routes/_authed.datasets.$id")).handle,
          })},
        ],
      },
      {
        path: "jobs",
        children: [
          { index: true, lazy: async () => ({
            Component: (await import("./routes/_authed.jobs._index")).default,
            handle: (await import("./routes/_authed.jobs._index")).handle,
          })},
          { path: "new", lazy: async () => ({
            Component: (await import("./routes/_authed.jobs.new")).default,
            handle: (await import("./routes/_authed.jobs.new")).handle,
          })},
          { path: ":id", lazy: async () => ({
            Component: (await import("./routes/_authed.jobs.$id")).default,
            handle: (await import("./routes/_authed.jobs.$id")).handle,
          })},
        ],
      },
      {
        path: "runs",
        children: [
          { index: true, lazy: async () => ({
            Component: (await import("./routes/_authed.runs._index")).default,
            handle: (await import("./routes/_authed.runs._index")).handle,
          })},
          { path: ":expId", lazy: async () => ({
            Component: (await import("./routes/_authed.runs.$expId")).default,
            handle: (await import("./routes/_authed.runs.$expId")).handle,
          })},
          { path: ":expId/:runId", lazy: async () => ({
            Component: (await import("./routes/_authed.runs.$expId.$runId")).default,
            handle: (await import("./routes/_authed.runs.$expId.$runId")).handle,
          })},
        ],
      },
      {
        path: "models",
        children: [
          { index: true, lazy: async () => ({
            Component: (await import("./routes/_authed.models._index")).default,
            handle: (await import("./routes/_authed.models._index")).handle,
          })},
        ],
      },
      {
        path: "profile",
        lazy: async () => ({
          Component: (await import("./routes/_authed.profile")).default,
          handle: (await import("./routes/_authed.profile")).handle,
        }),
      },
    ],
  },
  {
    path: "/",
    lazy: async () => ({ Component: (await import("./routes/_public")).default }),
    children: [
      {
        path: "login",
        lazy: async () => ({ Component: (await import("./routes/_public.login")).default }),
      },
    ],
  },
]);

export default function App() {
  useEffect(() => {
    setOn401(() => {
      queryClient.clear();
      window.location.href = "/login";
    });
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}
