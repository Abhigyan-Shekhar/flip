import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs/promises';
import {spawn} from 'node:child_process';

// The official runtime permits plain HTTP only on loopback. This local relay
// connects exclusively to the existing loopback-only host Switch service.
const target = {host:'flip-codex-egress',port:8080};
const relay = http.createServer((request,response)=>{
  const upstream=http.request({...target,path:request.url,method:request.method,headers:request.headers},res=>{
    response.writeHead(res.statusCode,res.headers);res.pipe(response);
  });
  upstream.on('error',()=>{if(!response.headersSent)response.writeHead(502);response.end();});
  request.on('error',()=>upstream.destroy());request.pipe(upstream);
});
relay.on('upgrade',(request,socket,head)=>{
  const upstream=net.connect(target,()=>{
    upstream.write(`${request.method} ${request.url} HTTP/1.1\r\n`);
    for(let i=0;i<request.rawHeaders.length;i+=2)upstream.write(`${request.rawHeaders[i]}: ${request.rawHeaders[i+1]}\r\n`);
    upstream.write('\r\n');if(head.length)upstream.write(head);
    socket.pipe(upstream);upstream.pipe(socket);
  });
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());
});
await new Promise(resolve=>relay.listen(8080,'127.0.0.1',resolve));
async function run(args){
  const child=spawn('node',['/opt/switch/agent-controller.mjs',...args],{stdio:'inherit'});
  const stop=()=>child.kill('SIGTERM');process.on('SIGTERM',stop);process.on('SIGINT',stop);
  const code=await new Promise(resolve=>child.on('exit',resolve));
  process.off('SIGTERM',stop);process.off('SIGINT',stop);return code;
}
let enrollment;
try{enrollment=JSON.parse(await fs.readFile('/state/enrollment.json','utf8'));}
catch(error){if(error.code!=='ENOENT')throw error;}
if(enrollment){
  const code=await run(['enroll','--server','http://127.0.0.1:8080','--code',enrollment.code,'--name',process.env.SWITCH_CONTROLLER_NAME||'flip-disposable-codex-container','--data-dir','/state','--secret-store','file']);
  if(code!==0)process.exit(code??1);
  await fs.unlink('/state/enrollment.json');
}
process.exit((await run(['run','--data-dir','/state','--shared-host-bundle','/opt/switch/shared-host.mjs','--updated-by-console']))??1);
