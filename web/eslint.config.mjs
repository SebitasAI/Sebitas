import nextConfig from "eslint-config-next";

// Next 16 ships `eslint-config-next` as a ready-to-spread flat config (no
// FlatCompat wrapper needed; trying to legacy-shim it throws a circular
// JSON error during serialisation). Anything custom goes in extra objects
// after the spread; this scaffold sticks to defaults.
const eslintConfig = [
  ...nextConfig,
];

export default eslintConfig;
