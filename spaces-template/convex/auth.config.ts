// Auth config: validate Clerk-issued JWTs.
//
// The issuer URL is set in the Convex deployment's environment variables
// (Convex dashboard -> Settings -> Environment Variables -> CLERK_JWT_ISSUER).
// It MUST match the JWT template named exactly "convex" in your Clerk
// instance (Clerk dashboard -> JWT Templates -> Convex).
//
// Without CLERK_JWT_ISSUER set on the Convex side, ctx.auth.getUserIdentity()
// returns null and assertSpaceAccess rejects every request -- the Space
// becomes unreachable. Set it before deploying this auth config.

export default {
  providers: [
    {
      domain: process.env.CLERK_JWT_ISSUER,
      applicationID: "convex",
    },
  ],
};
