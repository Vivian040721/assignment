module.exports = {
  appId: 'org.paperagent.workbench',
  productName: 'Paper Agent',
  asar: true,
  directories: {
    output: 'data/release/desktop',
    buildResources: 'electron/assets',
  },
  files: [
    'electron/dist/**/*',
    'frontend/dist/**/*',
    'package.json',
  ],
  extraResources: [
    {
      from: 'data/release/backend/PaperAgent-service',
      to: 'backend/PaperAgent-service',
      filter: ['**/*'],
    },
  ],
  win: {
    icon: 'electron/assets/icon.ico',
    target: [
      { target: 'nsis', arch: ['x64'] },
      { target: 'portable', arch: ['x64'] },
    ],
  },
  mac: {
    category: 'public.app-category.education',
    target: ['dmg', 'zip'],
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowElevation: true,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Paper Agent',
  },
  portable: {
    artifactName: '${productName}-${version}-portable.${ext}',
  },
};
