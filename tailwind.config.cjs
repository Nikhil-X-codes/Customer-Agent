/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./support_agent/web/**/*.html', './support_agent/web/**/*.js'],
  theme: {
    extend: {
      colors: {
        canvas: '#f5f7f8',
        ink: '#202a33',
        brand: '#203f5d',
        line: '#e2e8ec',
        muted: '#657580'
      }
    }
  },
  plugins: []
};
