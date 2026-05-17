/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        abyss: "#05060f",
        panel: "rgba(12, 15, 35, 0.76)",
        neon: {
          cyan: "#29f3ff",
          violet: "#a855f7",
          rose: "#fb2f7a",
          lime: "#a3ff12",
          amber: "#ffcc33"
        }
      },
      boxShadow: {
        glow: "0 0 45px rgba(41, 243, 255, 0.18)",
        violetGlow: "0 0 35px rgba(168, 85, 247, 0.22)",
        roseGlow: "0 0 35px rgba(251, 47, 122, 0.18)"
      },
      backgroundImage: {
        grid: "linear-gradient(rgba(41,243,255,.06) 1px, transparent 1px), linear-gradient(90deg, rgba(41,243,255,.06) 1px, transparent 1px)",
        radial: "radial-gradient(circle at top left, rgba(41,243,255,.22), transparent 32%), radial-gradient(circle at bottom right, rgba(168,85,247,.20), transparent 36%)"
      },
      keyframes: {
        pulseLine: {
          "0%, 100%": { opacity: ".35", transform: "scaleX(.85)" },
          "50%": { opacity: "1", transform: "scaleX(1)" }
        },
        drift: {
          "0%": { transform: "translate3d(0, 0, 0)" },
          "50%": { transform: "translate3d(12px, -10px, 0)" },
          "100%": { transform: "translate3d(0, 0, 0)" }
        }
      },
      animation: {
        pulseLine: "pulseLine 2.4s ease-in-out infinite",
        drift: "drift 8s ease-in-out infinite"
      }
    },
  },
  plugins: [],
};
