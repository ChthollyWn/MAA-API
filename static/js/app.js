// 引入 Vue 3 的 `ref` 函数
import { createApp, ref } from 'vue';

createApp({
  setup() {
    const message = ref('Hello Vue!');
    return {
      message
    };
  }
}).mount('#app');
