// A plain flat drawing of two doctors in white coats for the sign-in page: no photo, no download, no outside request.
const SKIN_A = '#e8b48f'
const SKIN_B = '#c68b64'
const HAIR_A = '#3b2a24'
const HAIR_B = '#2a2320'
const SCRUB_A = '#0b6f6a'
const SCRUB_B = '#1d5f9e'
const STETH = '#5b6e75'

export default function LoginDoctors() {
  return (
    <svg className="lg-doctors" viewBox="0 0 480 300" role="img" aria-label="Two doctors in white coats" preserveAspectRatio="xMidYMax meet">
      <circle cx="250" cy="196" r="150" className="lg-doctors-disc" />
      <g className="lg-doctors-cross" aria-hidden="true">
        <rect x="420" y="40" width="14" height="42" rx="3" />
        <rect x="406" y="54" width="42" height="14" rx="3" />
      </g>

      {/* doctor on the right, drawn first so the other stands in front */}
      <g>
        <path d="M232 300 C232 212 266 176 320 170 C374 176 408 212 408 300 Z" className="lg-coat" />
        <path d="M298 174 L320 224 L342 174 Z" fill={SCRUB_B} />
        <path d="M308 150 h24 v24 c-6 8 -18 8 -24 0z" fill={SKIN_B} />
        <ellipse cx="320" cy="118" rx="35" ry="39" fill={SKIN_B} />
        <path d="M285 112 C284 76 306 66 322 66 C344 66 358 80 356 112 C350 96 338 88 320 88 C302 88 290 98 285 112Z" fill={HAIR_B} />
        <g fill="none" stroke={HAIR_B} strokeWidth="2.4" strokeLinecap="round">
          <circle cx="307" cy="120" r="9" />
          <circle cx="333" cy="120" r="9" />
          <path d="M316 120 h8" />
          <path d="M311 138 Q320 145 329 138" />
        </g>
        <path d="M298 174 C292 206 302 228 320 230 C338 228 348 206 342 174" fill="none" stroke={STETH} strokeWidth="4" strokeLinecap="round" />
        <circle cx="320" cy="232" r="7" fill={STETH} />
        <path d="M298 174 L284 236 M342 174 L356 236" className="lg-coat-line" />
      </g>

      {/* doctor on the left */}
      <g>
        <path d="M82 300 C82 215 112 178 170 172 C228 178 258 215 258 300 Z" className="lg-coat" />
        <path d="M146 176 L170 226 L194 176 Z" fill={SCRUB_A} />
        <path d="M158 150 h24 v26 c-6 8 -18 8 -24 0z" fill={SKIN_A} />
        <circle cx="198" cy="72" r="15" fill={HAIR_A} />
        <ellipse cx="170" cy="120" rx="34" ry="38" fill={SKIN_A} />
        <path d="M134 118 C132 78 160 66 182 70 C208 74 210 100 206 118 C198 98 176 92 150 100 C142 104 136 110 134 118Z" fill={HAIR_A} />
        <g fill={HAIR_A}>
          <circle cx="158" cy="123" r="2.6" />
          <circle cx="182" cy="123" r="2.6" />
        </g>
        <path d="M160 138 Q170 146 180 138" fill="none" stroke={HAIR_A} strokeWidth="2.4" strokeLinecap="round" />
        <path d="M148 178 C140 214 150 238 170 240 C190 238 200 214 192 178" fill="none" stroke={STETH} strokeWidth="4" strokeLinecap="round" />
        <circle cx="170" cy="242" r="7" fill={STETH} />
        <path d="M146 176 L130 238 M194 176 L210 238" className="lg-coat-line" />
        <rect x="104" y="252" width="32" height="28" rx="3" className="lg-pocket" />
        <path d="M114 252 v-10 M120 252 v-12" stroke={SCRUB_A} strokeWidth="3" strokeLinecap="round" />
      </g>
    </svg>
  )
}
