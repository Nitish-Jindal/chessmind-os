import js from "@eslint/js";
import hooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": hooks },
    languageOptions: { parser: tseslint.parser },
    rules: {
      ...js.configs.recommended.rules,
      "react-hooks/rules-of-hooks": "error",
      // TypeScript's noUnusedLocals/noUnusedParameters and name resolution
      // cover these checks, including types, JSX and browser declarations.
      "no-unused-vars": "off",
      "no-undef": "off",
    },
  },
];
