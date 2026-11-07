import type { Theme } from '../../../../lib/theme';

export const brandName = 'flip-incident';
export const brand = {
  name: 'Flip Incident',
  colors: {
    primary: '#B9E66B', primaryLight: '#D3F19B', accent: '#EF8967',
    textDark: '#F4F2EA', textMedium: '#B3B9AD', textLight: '#7F887B',
    bgLight: '#151A16', bgDark: '#101410', bgOverlay: 'rgba(255,255,255,0.05)',
    divider: '#3A433B', shadow: 'rgba(0,0,0,0.45)',
  },
  fonts: { primary: 'Arial, Helvetica, sans-serif', mono: 'Menlo, Consolas, monospace' },
  spacing: { xs: 8, sm: 16, md: 24, lg: 48, xl: 80, xxl: 120 },
  borderRadius: { sm: 4, md: 8, lg: 12 },
  typography: {
    h1: { size: 88, weight: 700 }, h2: { size: 58, weight: 600 }, h3: { size: 38, weight: 600 },
    body: { size: 25, weight: 400 }, label: { size: 16, weight: 500, letterSpacing: 1 },
  },
  assets: { logo: undefined as string | undefined, logoLight: undefined as string | undefined },
};

export const brandTheme: Theme = {
  colors: brand.colors, fonts: brand.fonts, spacing: brand.spacing,
  borderRadius: brand.borderRadius, typography: brand.typography,
};
