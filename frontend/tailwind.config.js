/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        guin: {
          bg: "#0f172a",
          panel: "#111827",
          accent: "#14b8a6",
          muted: "#334155",
        },
      },
    },
  },
  darkMode: "class",
  plugins: [],
};
