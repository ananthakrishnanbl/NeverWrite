import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f5f3ee',
          100: '#e8e4d8',
          200: '#d1c9b4',
          300: '#b5a98a',
          400: '#9d8c68',
          500: '#8a7555',
          600: '#6e5c40',
          700: '#574835',
          800: '#3d3228',
          900: '#211b16',
        },
        paper: '#faf8f4',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      }
    }
  },
  plugins: []
} satisfies Config
