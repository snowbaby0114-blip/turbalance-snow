In the next stage of recruitment, we would like to test your technical knowledge of workload orchestration, including basic algorithmic understanding of different deployment strategies. Please show how you work through a technical task by preparing a solution to the following scenario.

## Task

Assume you have a customer who wants to balance the cloud state (w.r.t. memory) to minimize risks of killed pods. To test our custom load balancing algorithm

Set up a local minikube cluster with two nodes each having 2GB memory (other resources can be ignored). 
Note: On some OSes node capacity is not correctly set depending on the driver. There is no need to fix this. We will make sure that the scheduler will only account 2GB of memory by setting the environment variable NODE_MEM_LIMIT_MB. Just make sure that each node gets at least 2GB.

Deploy the custom scheduler (scheduler.py) and schedule the following pods in the same order using the custom scheduler: pod1, pod2, pod3
Note: You can verify that the scheduler is used by checking the logs.

Explain how the scheduler works and why pods are placed the way they are.


## Deliverable

The format is up to you. Feel free to provide the results in GitHub repo, zip, etc as soon as you are ready for the team to review. 

Please keep the time investment to no more than 1-2 hours to solve. The solution doesn’t need to be perfect and production-ready level, we just want an insight into how you work. We’re hoping to move forward to the next stage quickly 😊
