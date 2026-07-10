// utils/jwt.ts
//
// 纯解析：base64url 解码 JWT payload（不做签名校验 —— 服务端 enforce）。
// 用于：
// 1. axios 响应拦截器读 exp 字段做 proactive refresh；
// 2. useAuthSession 等需要看 claims 的场景。
//
// 不引入 jwt-decode 等第三方依赖，10 行代码搞定。

export interface JwtClaims {
  sub: string
  exp: number // epoch seconds
  iat: number
  username?: string
  roles?: string[]
  shelf_ids?: string[]
  type?: 'access' | 'refresh'
  ver?: number // refresh token 携带的轮转版本号
  [k: string]: unknown
}

/** 解 JWT payload；任何解析失败返回 null（不抛错，避免破坏拦截器主流程）。 */
export function decodeJwt(token: string): JwtClaims | null {
  if (!token) return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    // base64url → base64
    const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padLen = (4 - (padded.length % 4)) % 4
    const decoded = atob(padded + '='.repeat(padLen))
    return JSON.parse(decoded) as JwtClaims
  } catch {
    return null
  }
}

/** 距过期秒数（正数 = 还剩多久过期）；解析失败返回 null。 */
export function tokenExpiresIn(token: string): number | null {
  const claims = decodeJwt(token)
  if (!claims) return null
  return Math.floor(claims.exp - Date.now() / 1000)
}
