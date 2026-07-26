/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        navy: {
          950: '#071223',
          900: '#0a1a2e',
          850: '#0c2039',
          800: '#0f2742',
          750: '#13304f',
          700: '#17395c',
          600: '#1e4674',
        },
        ink: {
          DEFAULT: '#e9f0f9',
          muted: '#8fa8c6',
          dim: '#5d7798',
        },
        accent: {
          DEFAULT: '#2f8cf5',
          soft: '#1b4d85',
        },
        ok: '#2ee6a8',
        warn: '#f2a93b',
        bad: '#f2545b',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        slidein: {
          '0%': { opacity: '0', transform: 'translateY(-6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulsedot: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.35' },
        },
      },
      animation: {
        slidein: 'slidein 240ms ease-out',
        pulsedot: 'pulsedot 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
