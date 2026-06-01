/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Trading terminal dark theme
        surface: {
          DEFAULT: '#111827',
          50: '#1f2937',
          100: '#374151',
          200: '#4b5563',
        },
        bg: {
          DEFAULT: '#0a0e1a',
          secondary: '#0f1629',
        },
        bull: {
          DEFAULT: '#10b981',
          light: '#34d399',
          muted: '#065f46',
        },
        bear: {
          DEFAULT: '#ef4444',
          light: '#f87171',
          muted: '#7f1d1d',
        },
        warn: {
          DEFAULT: '#f59e0b',
          light: '#fbbf24',
          muted: '#78350f',
        },
        accent: {
          DEFAULT: '#3b82f6',
          light: '#60a5fa',
          muted: '#1e3a5f',
        },
        border: '#1f2937',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
