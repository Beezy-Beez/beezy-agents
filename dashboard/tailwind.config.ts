import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        cream: "#faf6ee",
        sidebar: "#1a1208",
        primary: "#8b4513",
        "primary-dark": "#6d3410",
        accent: "#d4a847",
        brown: "#2c2417",
        border: "#e8dcc8",
        muted: "#8b7355",
        "success-green": "#1e7e34",
        "warning-orange": "#e07b00",
        "error-red": "#c0392b",
        "blue-campaign": "#1a73e8",
        "blue-sniper": "#1558b0",
        "purple-hive": "#7b2d8b",
        "green-seo": "#1e7e34",
        "teal-audio": "#0e7c7b",
        "orange-sms": "#e07b00",
        "gray-flow": "#888888",
      },
      fontFamily: {
        sans: ["var(--font-dm-sans)", "Inter", "sans-serif"],
        serif: ["var(--font-dm-serif)", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
