#!/usr/bin/env python
import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped, Pose
from styx_msgs.msg import TrafficLightArray, TrafficLight
from styx_msgs.msg import Lane
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from light_classification.tl_classifier import TLClassifier
import tf
import yaml
import math

import tf

STATE_COUNT_THRESHOLD = 3

class TLDetector(object):
    def __init__(self):
        rospy.init_node('tl_detector')

        self.pose = None
        self.waypoints = None
        self.got_waypoints = False
        
        self.camera_image = None
        self.lights = []
        self.lights_map = []
        self.got_lights_map=False
        
        self.last_image_time = rospy.get_time()
        self.processing_image = False
        
        self.state = TrafficLight.UNKNOWN
        self.last_state = TrafficLight.UNKNOWN
        
        self.last_wp = -1
        self.state_count = 0

        self.bridge = CvBridge()
        self.light_classifier = TLClassifier()
        self.listener = tf.TransformListener()
        
        sub1 = rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb, queue_size=1)
        sub2 = rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        '''
        /vehicle/traffic_lights provides you with the location of the traffic light in 3D map space and
        helps you acquire an accurate ground truth data source for the traffic light
        classifier by sending the current color state of all traffic lights in the
        simulator. When testing on the vehicle, the color state will not be available. You'll need to
        rely on the position of the light and the camera image to predict it.
        '''
        sub3 = rospy.Subscriber('/vehicle/traffic_lights', TrafficLightArray, self.traffic_cb, queue_size=1, buff_size = 2*52428800)
        sub6 = rospy.Subscriber('/image_color', Image, self.image_cb)

        config_string = rospy.get_param("/traffic_light_config")
        self.config = yaml.load(config_string)
        self.site = self.config['is_site']
        
        #THe classifier is only invoked within a certain distance of the traffic light
        #The required paramters are different for site and simulator
        self.max_look_ahead_distance = 100.0
        self.min_look_ahead_distance = 25.0

        self.upcoming_red_light_pub = rospy.Publisher('/traffic_waypoint', Int32, queue_size=1)

        rospy.spin()

    def pose_cb(self, msg):
        self.pose = msg

    def waypoints_cb(self, waypoints):
        if not self.got_waypoints:
            self.waypoints = waypoints.waypoints
            self.got_waypoints = True
            
    def traffic_cb(self, msg):
        self.lights = msg.lights
            
    def image_cb(self, msg):
        """Identifies red lights in the incoming camera image and publishes the index
            of the waypoint closest to the red light's stop line to /traffic_waypoint

        Args:
            msg (Image): image from car-mounted camera

        """
        now_time = rospy.get_time()
        # limit image update to max 10 fps
        if (self.processing_image == False) and (now_time > (self.last_image_time + 0.1)):
            self.last_image_time = now_time
            
            self.has_image = True
            self.camera_image = msg
            
            #Try and except invoked to ensure a stop condition is passed to the path planner
            #if the traffic light classifier is not functioning
            #try:
            light_wp, state = self.process_traffic_lights()
            #except:
            #    light_wp = -1
            #    state = 0

            '''
            Publish upcoming red lights at camera frequency.
            Each predicted state has to occur `STATE_COUNT_THRESHOLD` number
            of times till we start using it. Otherwise the previous stable state is
            used.
            '''
            if self.state != state:
                self.state_count = 0
                self.state = state
            elif self.state_count >= STATE_COUNT_THRESHOLD:
                if self.state != self.last_state:
                    print("======= NEW PREDICTED LIGHT STATE =======")
                    print("LIGHT STATE: ",self.state)
                    self.last_state = self.state
                    
                light_wp = light_wp if state == TrafficLight.RED else -1
                self.last_wp = light_wp
                self.upcoming_red_light_pub.publish(Int32(light_wp))
            else:
                self.upcoming_red_light_pub.publish(Int32(self.last_wp))
            self.state_count += 1

    def get_closest_waypoint(self, pose):
        """Identifies the closest path waypoint to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to

        Returns:
            int: index of the closest waypoint in self.waypoints

        """
        diff_x = 1e6
        diff_y = 1e6
        
        if self.waypoints != None:
            
            for point in range(len(self.waypoints)):
                
                x = abs (pose.position.x - self.waypoints[point].pose.pose.position.x)
                y = abs (pose.position.y - self.waypoints[point].pose.pose.position.y)
                
                if  x < diff_x and y < diff_y:
                    diff_x = x
                    diff_y = y
                    ind = point        
                    
            return ind
        else:
            return

    def get_light_state(self, light):
        """Determines the current color of the traffic light

        Args:
            light (TrafficLight): light to classify

        Returns:
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        if(not self.has_image):
            self.prev_light_loc = None
            return False
        try:
            if self.light_classifier != None:
                pass
        except:
            return
        #Get Image
        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")

        #Get classification       
        return self.light_classifier.get_classification(cv_image)

    def process_traffic_lights(self):
        """Finds closest visible traffic light, if one exists, and determines its
            location and color

        Returns:
            int: index of waypoint closes to the upcoming stop line for a traffic light (-1 if none exists)
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        light = None

        if self.waypoints == None:
            return
        
        if not self.site:
            #determine direction of car travel
            q = [self.pose.pose.orientation.x, self.pose.pose.orientation.y, self.pose.pose.orientation.z, self.pose.pose.orientation.w]       
            theta = tf.transformations.euler_from_quaternion(q)[2]
            x = self.pose.pose.position.x
            x_in_front = x  + 1 * math.cos(theta)
            y = self.pose.pose.position.y
            y_in_front = y + 1 * math.sin(theta)

            traffic_light_found = False
            pred_state = TrafficLight.UNKNOWN        

            # run through all the lights and find out if there is a light close enough to be concerened about
            for light in self.lights:
                light_x = light.pose.pose.position.x
                light_y = light.pose.pose.position.y

                light_distance = (((light_x - x)**2 + (light_y - y)**2)**0.5)
                light_orient = (light_x - x + light_y - y)
                car_orient = x_in_front - x + y_in_front - y

                # prevent "referenced before assignment" error
                pred_state = -1
                #determine if the closest light is close enought to worry about
                #determine if we have passed the stop line
                #and determine that the light is infront of the car
                if  light_distance < self.max_look_ahead_distance and \
                    light_distance > self.min_look_ahead_distance and \
                    car_orient * light_orient > 1:

                    traffic_light_found = True
                    pred_state = self.get_light_state(light)

                    minimum_light_to_line_distance = 1e6
                    light_stop_pose = Pose()

                    #find stop line closest to light
                    for stop_line in self.config['stop_line_positions']:
                        light_to_stop_line_distance = (((light_x - stop_line[0])**2 + (light_y - stop_line[1])**2)**0.5)
                        if light_to_stop_line_distance < minimum_light_to_line_distance:
                            minimum_light_to_line_distance = light_to_stop_line_distance
                            light_stop_pose.position.x = stop_line[0]
                            light_stop_pose.position.y = stop_line[1]

                    #find waypoint corresponding to stop line
                    light_stop_wp = self.get_closest_waypoint(light_stop_pose)
                    break
        else:
            #stop_line = self.config['stop_line_positions']
            light_stop_pose = Pose()
            light_stop_pose.position.x = 8.0
            light_stop_pose.position.y = 16.2
            light_stop_wp = self.get_closest_waypoint(light_stop_pose)
            pred_state = self.get_light_state(light)
            traffic_light_found = True
        #if traffic light not found then we are free to travel else pass
        #light stop line and predicted light state
        if not traffic_light_found:          
            return -1, 4
        else:
            return light_stop_wp, pred_state


if __name__ == '__main__':
    try:
        TLDetector()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start traffic node.')
