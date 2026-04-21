import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        draft: {
          bg: "#fff7ed",
          border: "#fb923c",
          text: "#9a3412",
        },
      },
    },
  },
  plugins: [],
};

export default config;
