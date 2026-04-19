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
      {
        index: true,
        loader: () => redirect("/detectors"),
      },
    ],
  },
  {
    path: "/login",
    lazy: async () => ({ Component: (await import("./routes/_public.login")).default }),
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
