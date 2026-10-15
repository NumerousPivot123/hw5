import type { AgentName } from '../types'

interface Look {
  fur: string
  ear: string
  patch?: { color: string; side: 'left' | 'right' }
}

// Each agent's bulldog has its own coat and accessory so they're easy to tell apart.
const LOOKS: Record<AgentName, Look> = {
  boss: { fur: '#f8f3ea', ear: '#8a5a3c', patch: { color: '#c79262', side: 'right' } },
  inventory: { fur: '#dcaa6e', ear: '#9b6a3b' },
  accounting: { fur: '#a87a56', ear: '#6b4428', patch: { color: '#7d5436', side: 'left' } },
  facilities: { fur: '#f1e4cd', ear: '#b88a5c', patch: { color: '#d9b58a', side: 'left' } },
  customer_service: { fur: '#ffffff', ear: '#3a3a3a', patch: { color: '#3a3a3a', side: 'left' } },
}

const YALE = '#00356b'

function Accessory({ agent }: { agent: AgentName }) {
  switch (agent) {
    case 'boss':
      return (
        <g>
          {/* mortarboard */}
          <path d="M70 6 L118 22 L70 38 L22 22 Z" fill={YALE} />
          <rect x="48" y="26" width="44" height="14" rx="3" fill={YALE} />
          <path d="M70 22 L104 30 L106 50" stroke="#f5c542" strokeWidth="2.5" fill="none" />
          <circle cx="106" cy="52" r="4" fill="#f5c542" />
          {/* bow tie */}
          <path d="M70 118 L52 108 L52 128 Z M70 118 L88 108 L88 128 Z" fill={YALE} />
          <circle cx="70" cy="118" r="5" fill="#2a64a8" />
        </g>
      )
    case 'inventory':
      return (
        <g>
          {/* beanie */}
          <path d="M28 44 Q30 6 70 6 Q110 6 112 44 Z" fill={YALE} />
          <rect x="26" y="38" width="88" height="10" rx="5" fill="#ffffff" />
          <circle cx="70" cy="6" r="7" fill="#ffffff" />
          {/* shipping box */}
          <g transform="translate(100 98) rotate(-8)">
            <rect width="32" height="26" rx="2" fill="#c8915a" stroke="#8a5d33" strokeWidth="2" />
            <path d="M0 8 H32 M16 0 V8" stroke="#8a5d33" strokeWidth="2" />
          </g>
        </g>
      )
    case 'accounting':
      return (
        <g>
          {/* green eyeshade visor */}
          <path d="M24 40 Q70 18 116 40 L108 50 Q70 34 32 50 Z" fill="#2f8f5b" opacity="0.92" />
          <path d="M24 40 Q70 18 116 40" stroke="#1f6a42" strokeWidth="3" fill="none" />
          {/* round glasses */}
          <circle cx="52" cy="64" r="12" fill="none" stroke="#222" strokeWidth="3" />
          <circle cx="88" cy="64" r="12" fill="none" stroke="#222" strokeWidth="3" />
          <path d="M64 63 Q70 59 76 63" stroke="#222" strokeWidth="3" fill="none" />
        </g>
      )
    case 'facilities':
      return (
        <g>
          {/* hard hat */}
          <path d="M30 42 Q32 8 70 8 Q108 8 110 42 Z" fill="#f5c542" />
          <rect x="20" y="38" width="100" height="9" rx="4" fill="#e0ad22" />
          <rect x="64" y="10" width="12" height="30" rx="4" fill="#e0ad22" />
          {/* key on the collar */}
          <g transform="translate(76 116)">
            <circle cx="6" cy="6" r="6" fill="none" stroke="#c9a227" strokeWidth="3" />
            <path d="M12 6 H26 M22 6 V11 M26 6 V11" stroke="#c9a227" strokeWidth="3" />
          </g>
        </g>
      )
    case 'customer_service':
      return (
        <g>
          {/* headset */}
          <path d="M22 66 Q22 14 70 14 Q118 14 118 66" stroke="#222" strokeWidth="6" fill="none" />
          <rect x="12" y="56" width="16" height="24" rx="6" fill={YALE} />
          <rect x="112" y="56" width="16" height="24" rx="6" fill={YALE} />
          <path d="M20 78 Q24 100 52 100" stroke="#222" strokeWidth="3" fill="none" />
          <circle cx="54" cy="100" r="5" fill="#222" />
        </g>
      )
  }
}

export function Bulldog({ agent }: { agent: AgentName }) {
  const look = LOOKS[agent]
  const patchX = look.patch?.side === 'left' ? 50 : 90
  const collar = agent !== 'boss'
  return (
    <svg viewBox="0 0 140 140" className="bulldog-svg" role="img" aria-label={`${agent} bulldog`}>
      {/* ears */}
      <path d="M30 46 Q14 22 38 20 Q50 26 50 40 Z" fill={look.ear} />
      <path d="M110 46 Q126 22 102 20 Q90 26 90 40 Z" fill={look.ear} />
      {/* head */}
      <ellipse cx="70" cy="72" rx="47" ry="41" fill={look.fur} stroke="#00000022" strokeWidth="2" />
      {look.patch && <ellipse cx={patchX} cy="62" rx="17" ry="15" fill={look.patch.color} />}
      {/* brow wrinkles */}
      <path d="M54 46 Q70 40 86 46 M58 52 Q70 48 82 52" stroke="#00000030" strokeWidth="2" fill="none" />
      {/* eyes */}
      <circle cx="52" cy="64" r="6.5" fill="#1b1b1b" />
      <circle cx="88" cy="64" r="6.5" fill="#1b1b1b" />
      <circle cx="54.5" cy="61.5" r="2.2" fill="#fff" />
      <circle cx="90.5" cy="61.5" r="2.2" fill="#fff" />
      {/* muzzle and jowls */}
      <ellipse cx="70" cy="91" rx="31" ry="20" fill="#fff8ef" />
      <path d="M70 86 V96 M46 96 Q58 108 70 97 Q82 108 94 96" stroke="#3b2a22" strokeWidth="2.5" fill="none" strokeLinecap="round" />
      {/* nose */}
      <ellipse cx="70" cy="80" rx="12" ry="7.5" fill="#2a2222" />
      <ellipse cx="66" cy="78" rx="3.5" ry="2" fill="#ffffff55" />
      {/* underbite with two little teeth */}
      <path d="M56 103 Q70 114 84 103" fill="#e98f8f" stroke="#3b2a22" strokeWidth="2" />
      <path d="M60 104 L62 98 L64 105 Z M76 105 L78 98 L80 104 Z" fill="#fff" />
      {/* Yale collar with a Y tag */}
      {collar && (
        <g>
          <path d="M34 106 Q70 124 106 106 L108 114 Q70 132 32 114 Z" fill={YALE} />
          <circle cx="70" cy="126" r="8" fill="#ffffff" stroke={YALE} strokeWidth="2" />
          <text x="70" y="130" textAnchor="middle" fontSize="11" fontWeight="700" fill={YALE} fontFamily="Georgia, serif">Y</text>
        </g>
      )}
      <Accessory agent={agent} />
    </svg>
  )
}
