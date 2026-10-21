import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Outlet, RouterProvider, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { z } from "zod";
import { NewRunRoute } from "./routes/new-run";
import { RunRoute } from "./routes/run";

const rootRoute = createRootRoute({ component: () => <Outlet /> });
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: NewRunRoute });
const newRunRoute = createRoute({ getParentRoute: () => rootRoute, path: "/runs/new", component: NewRunRoute });
const runSearchSchema = z.object({ fixture: z.boolean().optional() });
const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$runId",
  validateSearch: (search) => runSearchSchema.parse(search),
  component: () => {
    const { runId } = runRoute.useParams();
    const { fixture } = runRoute.useSearch();
    return <RunRoute runId={runId} fixture={fixture} />;
  },
});

const routeTree = rootRoute.addChildren([indexRoute, newRunRoute, runRoute]);
export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register { router: typeof router }
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } });

export function App() {
  return <QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>;
}
