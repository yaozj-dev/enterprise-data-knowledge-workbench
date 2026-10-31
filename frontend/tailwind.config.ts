/**
 * Tailwind CSS 主题配置
 * 定义前端项目的字体、颜色和阴影扩展
 */
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"LXGW WenKai Screen"',
          '"Noto Sans SC"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          "sans-serif",
        ],
        mono: ['"JetBrains Mono"', '"SFMono-Regular"', "Consolas", "monospace"],
      },
      colors: {
        parchment: "#f4f7f6",
        ink: "#10252b",
        soot: "#20383e",
        moss: "#0d6b63",
        brass: "#c48635",
        tomato: "#c84b4b",
        mist: "#dce7e5",
      },
      boxShadow: {
        line: "0 1px 2px rgba(16, 37, 43, 0.06)",
        panel: "0 18px 50px rgba(16, 37, 43, 0.14)",
      },
    },
  },
  plugins: [],
} satisfies Config;
