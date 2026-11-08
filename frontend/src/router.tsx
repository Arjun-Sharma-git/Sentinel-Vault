// Routes. /login is public; everything else needs a session, and /admin/* needs an admin.
// The session is an HttpOnly cookie the browser can't read, so guards ask the API (GET /me).

import type { QueryClient } from '@tanstack/react-query'
import { createRootRouteWithContext, createRoute, createRouter, Outlet, redirect } from '@tanstack/react-router'
import { AppLayout } from './components/Layout'
import type { Me } from './lib/api'
import { meQuery } from './lib/queries'
import { AccessPage } from './routes/admin/Access'
import { AuditPage } from './routes/admin/Audit'
import { IndexingPage } from './routes/admin/Indexing'
import { PeoplePage } from './routes/admin/People'
import { ChatPage } from './routes/Chat'
import { LibraryPage } from './routes/Library'
import { LoginPage } from './routes/Login'

type RouterContext = { queryClient: QueryClient }

async function currentUser(queryClient: QueryClient): Promise<Me | null> {
  try {
    return await queryClient.ensureQueryData(meQuery)
  } catch {
    return null
  }
}

const rootRoute = createRootRouteWithContext<RouterContext>()({ component: () => <Outlet /> })

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/login',
  beforeLoad: async ({ context }) => {
    if (await currentUser(context.queryClient)) throw redirect({ to: '/' })
  },
  component: LoginPage,
})

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  beforeLoad: async ({ context }) => {
    const me = await currentUser(context.queryClient)
    if (!me) throw redirect({ to: '/login' })
    return { me }
  },
  component: AppLayout,
})

const chatRoute = createRoute({ getParentRoute: () => appRoute, path: '/', component: ChatPage })
const libraryRoute = createRoute({ getParentRoute: () => appRoute, path: '/library', component: LibraryPage })

const adminRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/admin',
  beforeLoad: ({ context }) => {
    if (!context.me.is_admin) throw redirect({ to: '/' })
  },
  component: () => <Outlet />,
})
const peopleRoute = createRoute({ getParentRoute: () => adminRoute, path: '/people', component: PeoplePage })
const accessRoute = createRoute({ getParentRoute: () => adminRoute, path: '/access', component: AccessPage })
const indexingRoute = createRoute({ getParentRoute: () => adminRoute, path: '/indexing', component: IndexingPage })
const auditRoute = createRoute({ getParentRoute: () => adminRoute, path: '/audit', component: AuditPage })

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    chatRoute,
    libraryRoute,
    adminRoute.addChildren([peopleRoute, accessRoute, indexingRoute, auditRoute]),
  ]),
])

export function buildRouter(queryClient: QueryClient) {
  return createRouter({ routeTree, context: { queryClient }, defaultPreload: 'intent' })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof buildRouter>
  }
}
