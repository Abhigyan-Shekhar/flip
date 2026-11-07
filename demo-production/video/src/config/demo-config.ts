import type { ProductDemoConfig, VideoConfig } from './types';

/** An 82-second truthful excerpt built from the dated, public PR #11 record. */
export const demoConfig: ProductDemoConfig = {
  product: {
    name: 'Flip',
    tagline: 'Your agents. Your team. One conversation.',
    description: 'The Green Check That Lied — historical incident report',
    website: 'flip-green-check-report-2026.dualheron.chatgpt.site',
    github: 'github.com/Abhigyan-Shekhar/flip/pull/11',
  },
  scenes: [
    {
      type: 'title', durationSeconds: 8,
      content: { headline: 'The green check that lied.', subheadline: 'Flip · PR #11 · Historical record · 6 November 2026' },
    },
    {
      type: 'problem', durationSeconds: 13,
      content: {
        headline: 'An empty result is not proof...',
        problems: [
          { icon: 'SCHEMA', text: 'Schema was not enough' },
          { icon: 'ARTIFACT', text: 'Artifact identity matters' },
          { icon: 'VERSION', text: 'Scanner version must exist' },
        ],
        codeExample: ['// CONCEPTUAL: native envelope omitted', '{ "Results": [] }', '// Empty findings alone do not prove clean'],
      },
    },
    {
      type: 'solution', durationSeconds: 11,
      content: {
        headline: 'Validate the envelope first.',
        description: 'Malformed or incomplete native Trivy reports fail closed.',
        highlights: ['Require the native report fields', 'Keep normalized formats working'],
      },
    },
    {
      type: 'demo', durationSeconds: 32,
      content: {
        type: 'video', videoFile: 'demos/incident-evidence-tour.mp4',
        label: 'Public historical evidence · captured 7 November 2026',
        caption: 'A real browser tour of the incident report and merged GitHub PR #11.',
      },
    },
    {
      type: 'feature', durationSeconds: 10,
      content: {
        headline: 'The regression test names the contract.',
        features: [
          { icon: '01', title: 'Native envelope', description: 'Schema, artifact, type, and Trivy version.' },
          { icon: '02', title: 'Fail closed', description: 'Missing or invalid fields cannot report clean.' },
          { icon: '03', title: 'Compatibility', description: 'Normalized findings, issues, and empty lists remain covered.' },
        ],
      },
    },
    {
      type: 'cta', durationSeconds: 8,
      content: {
        headline: 'Evidence before green.',
        tagline: 'The fix merged in PR #11 on 6 November 2026.',
        links: [{ type: 'github', label: 'Read the public PR', url: 'github.com/Abhigyan-Shekhar/flip/pull/11' }],
      },
    },
  ],
  audio: {},
  narrator: { enabled: false },
  captions: { enabled: false },
};

export const videoConfig: VideoConfig = { fps: 30, width: 1920, height: 1080 };

export function calculateTotalFrames(config: ProductDemoConfig, fps: number): number {
  return config.scenes.reduce((total, scene) => total + scene.durationSeconds * fps, 0);
}
