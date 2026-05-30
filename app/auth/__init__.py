"""HTTP authentication helpers.

Currently houses Clerk JWT verification (slice T-2). The pattern: a FastAPI
Depends chain that turns an `Authorization: Bearer <jwt>` header into a
validated `ClerkClaims` object and then into the corresponding AppUser.
"""
