// Query definitions shared by the router (guards) and components.

import { api } from './api'

export const meQuery = { queryKey: ['me'], queryFn: api.me, staleTime: 60_000 }
