import http from 'node:http';
import net from 'node:net';
const allowed=new Set(['chatgpt.com:443','api.openai.com:443','auth.openai.com:443']);
const proxy=http.createServer((req,res)=>{res.writeHead(403);res.end();});
proxy.on('connect',(request,client,head)=>{
  if(!allowed.has(request.url)){client.end('HTTP/1.1 403 Forbidden\r\n\r\n');return;}
  const [host,port]=request.url.split(':');
  const upstream=net.connect({host,port:Number(port)},()=>{
    client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    if(head.length)upstream.write(head);client.pipe(upstream);upstream.pipe(client);
  });
  upstream.on('error',()=>client.destroy());client.on('error',()=>upstream.destroy());
});
proxy.listen(3128,'0.0.0.0');
const target={host:'host.docker.internal',port:8080};
const relay=http.createServer((req,res)=>{
  const upstream=http.request({...target,path:req.url,method:req.method,headers:req.headers},response=>{
    res.writeHead(response.statusCode,response.headers);response.pipe(res);
  });
  upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});req.pipe(upstream);
});
relay.on('upgrade',(req,socket,head)=>{
  const upstream=net.connect(target,()=>{
    upstream.write(`${req.method} ${req.url} HTTP/1.1\r\n`);
    for(let i=0;i<req.rawHeaders.length;i+=2)upstream.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i+1]}\r\n`);
    upstream.write('\r\n');if(head.length)upstream.write(head);socket.pipe(upstream);upstream.pipe(socket);
  });
  upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());
});
relay.listen(8080,'0.0.0.0');
