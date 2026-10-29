import {defineConfig, loadEnv} from 'vite';
import {apiBase} from './src/lib/config';

export default defineConfig(({command, mode}) => {
  if (command === 'build') apiBase(loadEnv(mode, '.', 'VITE_').VITE_API_BASE_URL, true);
  return {};
});
