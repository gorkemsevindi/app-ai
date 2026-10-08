/** YourStars mark: gold star with a play triangle (vector rendition of the brand logo). */
export function StarMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-hidden="true">
      <defs>
        <linearGradient id="ys-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FFF1B0" />
          <stop offset=".45" stopColor="#F5C542" />
          <stop offset="1" stopColor="#B8860B" />
        </linearGradient>
      </defs>
      <polygon points="50,4 61.8,36.2 96,37.6 69,58.6 78.6,92 50,72.6 21.4,92 31,58.6 4,37.6 38.2,36.2" fill="url(#ys-gold)"
        stroke="#7a5a08" strokeWidth="1.5" strokeLinejoin="round" />
      <polygon points="43,40 64,52 43,64" fill="#1a1406" opacity=".85" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <StarMark />
      <span className="wordmark">YOURSTARS</span>
    </span>
  );
}
