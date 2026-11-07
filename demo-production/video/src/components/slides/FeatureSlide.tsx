import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';
import { useTheme } from '../../config/theme';
import type { FeatureContent } from '../../config/types';

type FeatureSlideProps = { content: FeatureContent };

export const FeatureSlide: React.FC<FeatureSlideProps> = ({ content }) => {
  const frame = useCurrentFrame();
  const theme = useTheme();
  return (
    <AbsoluteFill style={{ padding: 104, justifyContent: 'center', backgroundColor: theme.colors.bgLight }}>
      <p style={{ color: theme.colors.primary, font: '16px Menlo, monospace', letterSpacing: 2, marginBottom: 30 }}>THE REGRESSION CONTRACT</p>
      <h2 style={{ fontFamily: theme.fonts.primary, fontSize: 62, lineHeight: 1.08, letterSpacing: -2, color: theme.colors.textDark, maxWidth: 1200, margin: '0 0 44px' }}>{content.headline}</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 0, maxWidth: 1370 }}>
        {content.features.map((feature, index) => {
          const opacity = interpolate(frame, [24 + index * 13, 39 + index * 13], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
          return (
            <div key={feature.title} style={{ opacity, display: 'grid', gridTemplateColumns: '80px 330px 1fr', alignItems: 'center', gap: 16, padding: '22px 0', borderTop: '1px solid #3a433b' }}>
              <span style={{ color: theme.colors.primary, font: '16px Menlo, monospace' }}>{feature.icon}</span>
              <strong style={{ color: theme.colors.textDark, font: '600 26px Arial, sans-serif' }}>{feature.title}</strong>
              <span style={{ color: theme.colors.textMedium, font: '22px Arial, sans-serif' }}>{feature.description}</span>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
