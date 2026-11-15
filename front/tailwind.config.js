/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      screens: {
        'xs': '475px',
        'sm': '640px',
        'md': '768px',
        'lg': '1024px',
        'xl': '1280px',
        '2xl': '1536px',
      },
      colors: {
        // Paleta Tablero de gastos (2026-11-14): verde oscuro + crema.
        // primary-600 es el verde del boton (+ Registrar gasto) y
        // primary-700 su hover; blanco sobre ambos pasa contraste AA.
        primary: {
          50: '#eef5f0',
          100: '#dce9e0',
          200: '#bcd6c6',
          300: '#8fbda3',
          400: '#5fa07b',
          500: '#3e8560',
          600: '#2e6b4f',
          700: '#256b4a',
          800: '#1e4d36',
          900: '#173e2c',
          950: '#0f2a1e',
        },
        cream: '#f5f3ee',
        ink: '#1a1a1a',
      },
      spacing: {
        '18': '4.5rem',
        '88': '22rem',
        '128': '32rem',
      },
    },
  },
  plugins: [],
}